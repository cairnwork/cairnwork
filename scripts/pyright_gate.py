"""Exact-base Pyright gate: fail only on findings the PR adds relative to its merge base.

Usage:
  pyright_gate.py BASE.json BASE_ROOT HEAD.json HEAD_ROOT [TRUSTED_ROOT]
  pyright_gate.py --prepare-trees TRUSTED_ROOT BASE_ROOT HEAD_ROOT

A finding is identified by (relative file, rule, message, stripped source line), never by line
number, so inserting lines above old debt does not resurface it. Counts are compared per key, so
a second copy of an old finding is new. Exit 0 = nothing new, 1 = new findings, 2 = unreadable
input, changed/extending pyright config, file-level ignore/directive, or stub change (fail closed).

Fails closed (2) when either report analyzed no files, when pyrightconfig.json or the
[tool.pyright] table of pyproject.toml differs or uses extends, when any stub (.pyi / typings/ /
*-stubs/) is added/modified/deleted, or when any modified or new Python file contains a
file-level `# type: ignore` or `# pyright:` suppression directive. Single-line inline ignore
comments on code lines remain visible on the changed line for reviewers.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tokenize
import tomllib
from collections import Counter
from pathlib import Path

Key = tuple[str, str, str, str]
_SKIP_DIRS = {".git", ".venv"}
_MAX_FILE_BYTES = 8 * 1024 * 1024
_IGNORE_COMMENT = re.compile(r"#\s*(?:type|pyright)\s*:\s*ignore(?=$|[\s\[#])", re.I)
_PYRIGHT_DIRECTIVE = re.compile(r"#\s*pyright\s*:(?!\s*ignore(?=$|[\s\[#]))([^\n]*)", re.I)


class GateError(Exception):
    pass


def _source_line(path: Path, line: int) -> str:
    try:
        return path.read_text(encoding="utf-8").splitlines()[line].strip()
    except (OSError, UnicodeDecodeError, IndexError):
        return ""


def load(report: Path, root: Path) -> Counter[Key]:
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        diagnostics = data["generalDiagnostics"]
        if not data["summary"]["filesAnalyzed"]:
            raise ValueError("pyright analyzed no files")
        found: Counter[Key] = Counter()
        for item in diagnostics:
            if item["severity"] not in ("error", "warning"):
                continue
            path = Path(item["file"])
            rel = path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix()
            text = _source_line(root / rel, item["range"]["start"]["line"])
            found[(rel, item.get("rule", ""), item["message"], text)] += 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GateError(f"cannot read pyright report {report}: {exc!r}") from exc
    return found


def _read_bytes(path: Path, rel: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GateError(f"unreadable path {rel}")
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise GateError(f"file too large: {rel}")
        with path.open("rb") as handle:
            data = handle.read(_MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise GateError(f"cannot read {rel}: {exc!r}") from exc
    if len(data) > _MAX_FILE_BYTES:
        raise GateError(f"file too large: {rel}")
    return data


def _pyproject_pyright(pyproj: Path) -> object:
    if pyproj.is_symlink() or (pyproj.exists() and not pyproj.is_file()):
        raise GateError(f"{pyproj.name} must be a regular file")
    if not pyproj.exists():
        return None
    try:
        text = _read_bytes(pyproj, pyproj.name).decode("utf-8")
        table = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read {pyproj.name}: {exc!r}") from exc
    if not isinstance(table, dict):
        raise GateError(f"invalid {pyproj.name}")
    tool = table.get("tool")
    if tool is not None and not isinstance(tool, dict):
        raise GateError(f"invalid [tool] table in {pyproj.name}")
    return tool.get("pyright") if isinstance(tool, dict) else None


def _config(root: Path) -> tuple[str | None, object]:
    cfg = root / "pyrightconfig.json"
    if cfg.is_symlink() or (cfg.exists() and not cfg.is_file()):
        raise GateError("pyrightconfig.json must be a regular file")
    try:
        text = _read_bytes(cfg, cfg.name).decode("utf-8") if cfg.is_file() else None
    except UnicodeDecodeError as exc:
        raise GateError(f"cannot read pyrightconfig.json: {exc!r}") from exc
    return text, _pyproject_pyright(root / "pyproject.toml")


def _extends(text: str | None, table: object) -> bool:
    # pyrightconfig.json may carry comments, so match the key textually rather than parse it.
    return bool(text and '"extends"' in text) or (isinstance(table, dict) and "extends" in table)


def _file_level_ignores(text: str, rel: str) -> Counter[str]:
    if "\x00" in text:
        raise GateError(f"NUL byte in {rel}")
    norm = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    found: Counter[str] = Counter()
    seen_code = False
    skip = (
        tokenize.ENCODING,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    )
    try:
        for tok in tokenize.generate_tokens(io.StringIO(norm).readline):
            if tok.type in skip:
                continue
            if tok.type == tokenize.COMMENT:
                own_line = tok.line[: tok.start[1]].lstrip("\ufeff").strip() == ""
                for match in _PYRIGHT_DIRECTIVE.finditer(tok.string):
                    directive = " ".join(match.group(1).lower().split())
                    found[f"pyright:{directive}"] += 1
                if (not seen_code or own_line) and _IGNORE_COMMENT.search(tok.string):
                    for match in _IGNORE_COMMENT.finditer(tok.string):
                        found[" ".join(match.group(0).lower().split())] += 1
                continue
            seen_code = True
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise GateError(f"cannot tokenize {rel}: {exc!r}") from exc
    return found


def _is_stub(rel: str, suffix: str) -> bool:
    parts = Path(rel).parts
    return suffix == ".pyi" or any(
        p.lower() == "typings" or p.lower().endswith("-stubs") for p in parts
    )


def _walk_error(exc: OSError) -> None:
    raise GateError(f"cannot walk tree: {exc!r}") from exc


def _scan_tree(root: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    stubs: dict[str, bytes] = {}
    py_files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_error, followlinks=False):
        kept: list[str] = []
        for d in sorted(dirnames):
            if d in _SKIP_DIRS:
                continue
            dpath = Path(dirpath) / d
            if dpath.is_symlink():
                raise GateError(f"symlinked directory at {dpath.relative_to(root).as_posix()}")
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            parts = Path(rel).parts
            if name == "pyrightconfig.json" and len(parts) > 1:
                raise GateError(f"nested pyrightconfig.json at {rel}")
            if name == "pyproject.toml" and len(parts) > 1:
                if _pyproject_pyright(path) is not None:
                    raise GateError(f"nested [tool.pyright] at {rel}")
            if _is_stub(rel, path.suffix.lower()):
                stubs[rel] = _read_bytes(path, rel)
            elif path.suffix == ".py":
                py_files[rel] = _read_bytes(path, rel)
    return stubs, py_files


def check_trees(base_root: Path, head_root: Path, trusted_root: Path | None = None) -> None:
    configs = [_config(base_root), _config(head_root)]
    if any(_extends(*config) for config in configs) or (
        trusted_root is not None and _extends(*_config(trusted_root))
    ):
        raise GateError("pyright config uses extends; a human must review")
    if configs[0] != configs[1]:
        raise GateError("pyright configuration changed; a human must review this PR")
    base_stubs, base_py = _scan_tree(base_root)
    head_stubs, head_py = _scan_tree(head_root)
    if base_stubs != head_stubs:
        raise GateError("type stubs changed; a human must review this PR")
    for rel, head_raw in sorted(head_py.items()):
        if base_py.get(rel) == head_raw:
            continue
        try:
            head_text = head_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError(f"non-UTF-8 python file {rel}") from exc
        if _file_level_ignores(head_text, rel):
            raise GateError(f"file-level pyright/type ignore in modified or new file {rel}")


def prepare_trees(trusted_root: Path, base_root: Path, head_root: Path) -> None:
    check_trees(base_root, head_root, trusted_root)
    trusted_text, trusted_table = _config(trusted_root)
    if trusted_text is not None:
        payload = trusted_text
    elif trusted_table is not None:
        if not isinstance(trusted_table, dict):
            raise GateError("invalid [tool.pyright] table in trusted pyproject.toml")
        payload = json.dumps(trusted_table)
    else:
        payload = "{}"
    for root in (base_root, head_root):
        (root / "pyrightconfig.json").write_text(payload, encoding="utf-8")


def new_findings(base: Counter[Key], head: Counter[Key]) -> list[Key]:
    extra = head - base
    return sorted(extra.elements())


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--prepare-trees":
        if len(argv) != 4:
            print(__doc__)
            return 2
        try:
            prepare_trees(Path(argv[1]), Path(argv[2]), Path(argv[3]))
        except (GateError, OSError, ValueError, TypeError) as exc:
            print(f"pyright gate failed closed: {exc}")
            return 2
        return 0
    if len(argv) not in (4, 5):
        print(__doc__)
        return 2
    base_root, head_root = Path(argv[1]), Path(argv[3])
    trusted_root = Path(argv[4]) if len(argv) == 5 else None
    try:
        check_trees(base_root, head_root, trusted_root)
        base = load(Path(argv[0]), base_root.resolve())
        head = load(Path(argv[2]), head_root.resolve())
    except (GateError, OSError, ValueError) as exc:
        print(f"pyright gate failed closed: {exc}")
        return 2
    added = new_findings(base, head)
    print(f"pyright: base {sum(base.values())} findings, head {sum(head.values())} findings")
    for rel, rule, message, text in added:
        print(f"NEW {rel} [{rule}] {message}\n    {text}")
    if added:
        print(f"{len(added)} new pyright finding(s) introduced by this change")
        return 1
    print("no new pyright findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
