"""The listing and walk behind bytecode_roots.py, run as the run's own user in its own process.

bytecode_roots.py runs this file's source with ``python -I -S -c <source> <spec json>``: as the
owner in owner mode, as the verifier account under one, so that what it can open is what the
run's Python could open. Standard library only; nothing from the checkout or the run is
imported. It prints one JSON object and exits 0; any error it cannot place fails the run.

Directories are identified by (st_dev, st_ino), never by spelling: the kernel resolves letter
case and NFC/NFD on APFS, firmlinks (/System/Volumes/Data), symlinks and ``..`` that a string
comparison or realpath does not canonicalize (#289 review).

1. **Listing.** Both write roots are listed without following a symlink; a directory closed to
   its owner is opened (u+rwx) first. Each directory becomes a node with its entries as edges:
   subdirectories, and symlinks whose target (``os.stat``, as the kernel resolves it) is a
   directory. Every pyc-like name is reported, so is every error.
2. **Graph.** When there is a pyc, the ancestors of the roots join as nodes (their entries are
   edges too, so /var -> /private/var counts), and every node gets a ``..`` edge to the
   directory the kernel gives for ``<node>/..``. Only nodes from which a pyc can be reached are
   walked.
3. **Walk.** Python reads ``<P>/<D>/<name>`` as bytecode for a source in directory D, and the
   lookup resolves P's part through these nodes. So the walk starts at every node P with D = /
   and, for each edge, steps D by every entry of D whose name folds (NFD, case) to the edge's
   name, or by ``..``; states are (node, D) by identity, so loops end. Where a node holding a pyc
   is reached with D, each existing source in D the pyc's name can stand for (a stem cut at a dot
   plus ``.py``, folded) is a hit, and when D was entered as ``__pycache__`` the sources of the
   directory before it too (the legacy layout, read without a prefix: P = /).

Not modeled: a lookup that leaves the run's directories and their ancestors for another
directory and comes back into them by ``..`` or a system link (the run cannot write there).
"""

import errno
import json
import os
import stat
import sys
import unicodedata
from collections import deque

MAX_PATH = 4096  # Linux PATH_MAX (macOS: 1024): no longer path can be opened
NOFOLLOW = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}


def fold(name):
    """A name as APFS compares it: canonical decomposition and case folding."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def pyc_like(name):
    folded = unicodedata.normalize("NFKD", name).casefold()
    return "".join(c for c in folded if c.isascii()).endswith(".pyc")


def ident(info):
    return (info.st_dev, info.st_ino)


def opened(path):
    """The entries of directory ``path`` (never a symlink), opened first if closed to its owner."""
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, "not a directory")
    if info.st_mode & 0o700 != 0o700:
        os.chmod(path, stat.S_IMODE(info.st_mode) | 0o700, **NOFOLLOW)
    with os.scandir(path) as listing:
        return ident(info), list(listing)


def dir_ident(path):
    """The identity of the directory ``path`` resolves to, or None (not one, or not reachable)."""
    if len(path) >= MAX_PATH:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None  # missing, a loop, or closed to this user: the run's Python cannot use it
    return ident(info) if stat.S_ISDIR(info.st_mode) else None


def list_roots(roots, limit, out):
    """Nodes {id: path}, edges {id: [(name, id)]}, pycs {id: [(index, rel, name)]}, listed
    {id: (index, rel)}."""
    nodes, edges, pycs, listed, count = {}, {}, {}, {}, 0
    stack = [(index, "") for index in range(len(roots))]
    while stack:
        index, rel = stack.pop()
        path = os.path.join(roots[index], rel) if rel else roots[index]
        try:
            here, entries = opened(path)
        except OSError as exc:
            out["errors"].append([index, rel, exc.strerror or str(exc)])
            continue
        nodes[here], listed[here] = path, (index, rel)
        edges.setdefault(here, [])
        for entry in entries:
            count += 1
            if count > limit:
                out["over"] = True
                return nodes, edges, pycs, listed
            sub = rel + "/" + entry.name if rel else entry.name
            if pyc_like(entry.name):
                out["pycs"].append([index, sub])
                pycs.setdefault(here, []).append((index, sub, entry.name))
            try:
                if entry.is_symlink():
                    target = dir_ident(entry.path)
                    if target is not None:
                        edges[here].append((entry.name, target))
                elif entry.is_dir(follow_symlinks=False):
                    edges[here].append((entry.name, ident(entry.stat(follow_symlinks=False))))
                    stack.append((index, sub))
            except OSError as exc:
                out["errors"].append([index, sub, exc.strerror or str(exc)])
    return nodes, edges, pycs, listed


def add_ancestors(roots, nodes, edges):
    """The roots' ancestors as nodes, their entries that resolve to a node as edges, and a
    ``..`` edge on every node."""
    for root in roots:
        path = os.path.dirname(os.path.realpath(root))
        while True:
            here = dir_ident(path)
            if here is not None and here not in nodes:
                nodes[here] = path
            if path == "/":
                break
            path = os.path.dirname(path)
    for here, path in list(nodes.items()):
        if here in edges:
            continue  # a listed directory: its entries are edges already
        try:
            names = os.listdir(path)
        except OSError:
            names = []  # not listable by this user: only its chain below counts
        for name in names:
            target = dir_ident(os.path.join(path, name))
            if target in nodes:
                edges.setdefault(here, []).append((name, target))
        edges.setdefault(here, [])
    for here, path in nodes.items():
        parent = dir_ident(os.path.join(path, ".."))
        if parent in nodes:
            edges[here].append(("..", parent))


def useful_nodes(edges, pycs):
    parents = {}
    for here, out_edges in edges.items():
        for _, child in out_edges:
            parents.setdefault(child, set()).add(here)
    useful, todo = set(pycs), list(pycs)
    while todo:
        for parent in parents.get(todo.pop(), ()):
            if parent not in useful:
                useful.add(parent)
                todo.append(parent)
    return useful


class Side:
    """The D side of the walk: real directories, their entries folded, cached by identity."""

    def __init__(self):
        self.entries = {}

    def names(self, path, here):
        if here not in self.entries:
            folded = {}
            try:
                for name in os.listdir(path):
                    folded.setdefault(fold(name), []).append(name)
            except OSError:
                folded = None  # searchable but not listable: try the spelling as given
            self.entries[here] = folded
        return self.entries[here]

    def matches(self, path, here, name):
        """(name, path, id) of each directory in D a lookup of ``name`` may resolve to."""
        if name == "..":
            spelled = [".."]
        else:
            folded = self.names(path, here)
            spelled = [name] if folded is None else folded.get(fold(name), [])
        for entry in spelled:
            step = os.path.join(path, entry)
            target = dir_ident(step)
            if target is not None:
                yield entry, step, target

    def sources(self, path, here, pyc_name):
        """The existing sources in D a pyc named ``pyc_name`` can stand for."""
        stem = pyc_name[: -len(".pyc")]
        wanted = {fold(stem[:i] + ".py") for i, char in enumerate(stem) if char == "."}
        folded = self.names(path, here)
        if folded is None:
            candidates = [stem[:i] + ".py" for i, char in enumerate(stem) if char == "."]
        else:
            candidates = [name for key in wanted for name in folded.get(key, [])]
        return sorted(n for n in set(candidates) if os.path.exists(os.path.join(path, n)))


def walk(nodes, edges, pycs, listed, max_states, out):
    useful = useful_nodes(edges, pycs)
    side, top = Side(), dir_ident("/")
    states = deque((node, "/", top, None) for node in useful)
    seen = {(node, top, None) for node in useful}
    budget, hits = len(seen) + max_states, set()
    while states:
        node, path, here, before = states.popleft()
        for index, rel, name in pycs.get(node, ()):
            for d_path, d_id in ((path, here), *([before] if before else [])):
                names = side.sources(d_path, d_id, name)
                if names:
                    where = listed.get(d_id, (-1, ""))
                    hits.add((index, rel, d_path, *where, tuple(names)))
        for name, child in edges.get(node, ()):
            if child not in useful:
                continue
            for entry, step, target in side.matches(path, here, name):
                legacy = (path, here) if fold(entry) == fold("__pycache__") else None
                if (child, target, legacy) in seen:
                    continue
                seen.add((child, target, legacy))
                if len(seen) > budget:
                    out["steps_over"] = True
                    return hits
                states.append((child, step, target, legacy))
    return hits


def main(argv):
    spec = json.loads(argv[1])
    roots = spec["roots"]
    out = {"pycs": [], "hits": [], "errors": [], "over": False, "steps_over": False}
    nodes, edges, pycs, listed = list_roots(roots, spec["limit"], out)
    if pycs and not out["over"]:
        add_ancestors(roots, nodes, edges)
        hits = walk(nodes, edges, pycs, listed, spec["max_states"], out)
        out["hits"] = [[*hit[:-1], list(hit[-1])] for hit in sorted(hits)]
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv)
