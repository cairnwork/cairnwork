"""The listing and walk behind bytecode_roots.py, run as the run's own user in its own process.

bytecode_roots.py runs this file's source with ``python -I -S -c <source> <spec json>``: as the
owner in owner mode, as the verifier account under one, so that what it can open is what the
run's Python could open. Standard library only; nothing from the checkout or the run is
imported. It prints one JSON object and exits 0; any error it cannot place fails the run.

Directories are identified by (st_dev, st_ino), never by spelling: the kernel resolves letter
case and NFC/NFD on APFS, firmlinks (/System/Volumes/Data), symlinks and ``..`` that a string
comparison or realpath does not canonicalize (#289 review).

1. **Listing.** Both write roots are listed without following a symlink; a directory closed to
   its owner is opened (u+rwx) first, and one still not listable and searchable (an ACL) is an
   error. Each directory becomes a node with its entries as edges:
   subdirectories, and symlinks whose target (resolved by the kernel relative to the link's own
   directory, after the whole listing has opened every closed directory in the roots) is a
   directory. Every pyc-like name is reported, so is every other regular file that starts with
   this Python's magic number (the run's Python names bytecode by ``BYTECODE_SUFFIXES``,
   ``sys.implementation.cache_tag`` and pytest's ``PYC_TAIL``, all of which the run can change;
   the owner judges only those outside the checkout's committed files), so is every error, a
   symlink's included unless its target is missing, closed (by then only outside the roots),
   looping or through a file (link_target).
2. **Graph.** When there is a pyc, the ancestors of the roots join as nodes (their entries are
   edges too, so /var -> /private/var counts; the step down towards each root is an edge even
   where an ancestor cannot be listed), and every node gets a ``..`` edge to the
   directory the kernel gives for ``<node>/..``. Only nodes from which a pyc can be reached are
   walked.
3. **Walk.** Python reads ``<P>/<D>/<name>`` as bytecode for a source in directory D, and the
   lookup resolves P's part through these nodes. So the walk starts at every node P with D = /
   and, for each edge, steps D by every entry of D whose name folds (NFD, case) to the edge's
   name, or by ``..``; states are (node, D) by identity, so loops end. Where a node holding a pyc
   is reached with D, each existing source in D the pyc's name can stand for (a stem cut at a dot
   plus ``.py``, folded) is a hit, and when D was entered as ``__pycache__`` from the directory
   P's side stood in (no prefix: P = /), the sources of that directory too (the legacy layout,
   which Python never reads at ``<P>/<D>/__pycache__`` under another prefix). Only the first
   spelling of D reached is kept, so one the kernel refuses as too long is an error, not a miss.

Not modeled: a lookup that leaves the run's directories and their ancestors for another
directory and comes back into them by ``..`` or a system link (the run cannot write there).
"""

import errno
import importlib.util
import json
import os
import stat
import sys
import unicodedata
from collections import deque

MAX_PATH = 4096  # Linux PATH_MAX (macOS: 1024): no longer path can be opened
NOFOLLOW = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
MAGIC = importlib.util.MAGIC_NUMBER


def fold(name):
    """A name as APFS compares it: canonical decomposition and case folding."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def pyc_like(name):
    folded = unicodedata.normalize("NFKD", name).casefold()
    return "".join(c for c in folded if c.isascii()).endswith(".pyc")


def ident(info):
    return (info.st_dev, info.st_ino)


def bytecode(name, fd):
    """Whether the regular file ``name`` in directory ``fd`` starts with this Python's magic
    number. A file closed to this user is not read: like one removed before the run ended, it is
    the known gap of bytecode_roots.py."""
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except PermissionError:
        return False
    try:
        return stat.S_ISREG(os.fstat(handle).st_mode) and os.read(handle, len(MAGIC)) == MAGIC
    finally:
        os.close(handle)


def opened(path):
    """The identity of directory ``path`` (never a symlink), opened first if closed to its owner,
    and its entries as (name, kind, identity or None, error or None), kind "dir" (identity set),
    "link", "bytecode" (a file starting as bytecode, not named like it) or "other". Listed
    through a descriptor, so each entry is resolved relative to its own directory, not by the
    listing's (possibly long) spelling. A directory that stays unlistable
    or unsearchable after the mode fix (a macOS ACL survives it) is an error, so every directory
    of the roots is both, or already a finding."""
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, "not a directory")
    if info.st_mode & 0o700 != 0o700:
        os.chmod(path, stat.S_IMODE(info.st_mode) | 0o700, **NOFOLLOW)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if ident(os.fstat(fd)) != ident(info):
            raise OSError(errno.ESTALE, "replaced while listed")
        if not os.access(".", os.X_OK, dir_fd=fd, effective_ids=True):  # e.g. an ACL deny search
            raise PermissionError(errno.EACCES, "listable but not searchable")
        found = []
        with os.scandir(fd) as listing:  # DirEntry calls use fd: it stays open until done
            for entry in listing:
                try:
                    if entry.is_symlink():  # resolved once the listing is done (resolve_links)
                        found.append((entry.name, "link", None, None))
                    elif entry.is_dir(follow_symlinks=False):
                        found.append(
                            (entry.name, "dir", ident(entry.stat(follow_symlinks=False)), None)
                        )
                    elif (
                        entry.is_file(follow_symlinks=False)
                        and not pyc_like(entry.name)
                        and bytecode(entry.name, fd)
                    ):
                        found.append((entry.name, "bytecode", None, None))
                    else:
                        found.append((entry.name, "other", None, None))
                except OSError as exc:
                    found.append((entry.name, "other", None, exc.strerror or str(exc)))
        return ident(info), found
    finally:
        os.close(fd)


def link_target(name, fd):
    """The identity of the directory the symlink ``name`` in directory ``fd`` resolves to, or None.
    Resolved relative to its own directory, so no lookup reaching the link resolves with fewer
    steps, and only after the whole listing (resolve_links), when every closed directory in the
    roots has been opened and found searchable (opened). ENOENT (dangling), ENOTDIR (through a
    file), ELOOP (a loop: any longer lookup through it loops too) and EACCES (now only a directory
    outside the roots, which the run cannot change, closed to this user and so to the run's
    Python) mean the run's Python cannot use it either; any other error is raised and reported."""
    try:
        info = os.stat(name, dir_fd=fd)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.ELOOP):
            return None
        raise
    return ident(info) if stat.S_ISDIR(info.st_mode) else None


def resolve_links(links, edges, out):
    """Second pass over the symlinks of the listing ({(id, dir path): [(index, rel, name)]}):
    each resolving to a directory becomes an edge; an error is an error row."""
    for (here, path), names in links.items():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            out["errors"].extend([index, rel, exc.strerror or str(exc)] for index, rel, _ in names)
            continue
        try:
            if ident(os.fstat(fd)) != here:
                out["errors"].extend(
                    [index, rel, "replaced while listed"] for index, rel, _ in names
                )
                continue
            for index, rel, name in names:
                try:
                    target = link_target(name, fd)
                except OSError as exc:
                    out["errors"].append([index, rel, exc.strerror or str(exc)])
                    continue
                if target is not None:
                    edges[here].append((name, target))
        finally:
            os.close(fd)


def dir_ident(path):
    """The identity of the directory ``path`` resolves to, or None (not one, or not reachable).
    A spelling too long to resolve is raised: a shorter one of the same place may be usable."""
    if len(path) >= MAX_PATH:
        raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG))
    try:
        info = os.stat(path)
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            raise
        return None  # missing, a loop, or closed to this user: the run's Python cannot use it
    return ident(info) if stat.S_ISDIR(info.st_mode) else None


def present(path):
    """Whether ``path`` exists as the run's Python would see it. Missing, through a file, closed
    or a loop is no; any other error (a spelling too long) is raised."""
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.ELOOP):
            return False
        raise
    return True


def list_roots(roots, limit, out):
    """Nodes {id: path}, edges {id: [(name, id)]}, pycs {id: [(index, rel, name)]}, listed
    {id: (index, rel)}."""
    nodes, edges, pycs, listed, links, count = {}, {}, {}, {}, {}, 0
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
        for name, kind, target, error in entries:
            count += 1
            if count > limit:
                out["over"] = True
                return nodes, edges, pycs, listed
            sub = rel + "/" + name if rel else name
            if pyc_like(name):
                out["pycs"].append([index, sub])
                pycs.setdefault(here, []).append((index, sub, name))
            elif kind == "bytecode":
                out["bytecode"].append([index, sub])
            if error is not None:
                out["errors"].append([index, sub, error])
            elif kind == "link":
                links.setdefault((here, path), []).append((index, sub, name))
            elif target is not None:
                edges[here].append((name, target))
                stack.append((index, sub))
    resolve_links(links, edges, out)
    return nodes, edges, pycs, listed


def add_ancestors(roots, nodes, edges):
    """The roots' ancestors as nodes, their entries that resolve to a node as edges, and a
    ``..`` edge on every node."""
    chain = []  # (ancestor, name, child): each step down to a root, listable or not
    for root in roots:
        below, path = dir_ident(root), os.path.realpath(root)
        while path != "/":
            name, path = os.path.basename(path), os.path.dirname(path)
            here = dir_ident(path)
            if here is None or below is None:
                break
            nodes.setdefault(here, path)
            chain.append((here, name, below))
            below = here
    for here, path in list(nodes.items()):
        if here in edges:
            continue  # a listed directory: its entries are edges already
        try:
            names = os.listdir(path)
        except OSError:
            names = []  # not listable by this user: the chain edges below still count
        for name in names:
            target = dir_ident(os.path.join(path, name))
            if target in nodes:
                edges.setdefault(here, []).append((name, target))
        edges.setdefault(here, [])
    for here, name, below in chain:
        if (name, below) not in edges[here]:
            edges[here].append((name, below))
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
        return sorted(n for n in set(candidates) if present(os.path.join(path, n)))


def walk(nodes, edges, pycs, listed, max_states, out):
    useful = useful_nodes(edges, pycs)
    side, top = Side(), dir_ident("/")
    # (node, D spelling, D identity, legacy (path, identity) or None)
    states: deque[tuple] = deque((node, "/", top, None) for node in useful)
    seen: set[tuple] = {(node, top, None) for node in useful}
    budget, hits = len(seen) + max_states, set()
    while states:
        node, path, here, before = states.popleft()
        for index, rel, name in pycs.get(node, ()):
            for d_path, d_id in ((path, here), *([before] if before else [])):
                try:
                    names = side.sources(d_path, d_id, name)
                except OSError as exc:
                    out["errors"].append([index, rel, exc.strerror or str(exc)])
                    continue
                if names:
                    where = listed.get(d_id, (-1, ""))
                    hits.add((index, rel, d_path, *where, tuple(names)))
        for name, child in edges.get(node, ()):
            if child not in useful:
                continue
            try:
                matched = list(side.matches(path, here, name))
            except OSError as exc:
                where = listed.get(node) or listed.get(here) or (0, "")
                out["errors"].append([*where, exc.strerror or str(exc)])
                continue
            for entry, step, target in matched:
                # the legacy layout is read only without a prefix: where P's side and D are one
                # directory (P = /), not at <P>/<D>/__pycache__ under any other prefix
                pycache = fold(entry) == fold("__pycache__") and node == here
                legacy = (path, here) if pycache else None
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
    out = {"pycs": [], "bytecode": [], "hits": [], "errors": [], "over": False, "steps_over": False}
    nodes, edges, pycs, listed = list_roots(roots, spec["limit"], out)
    if pycs and not out["over"]:
        add_ancestors(roots, nodes, edges)
        hits = walk(nodes, edges, pycs, listed, spec["max_states"], out)
        out["hits"] = [[*hit[:-1], list(hit[-1])] for hit in sorted(hits)]
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv)
