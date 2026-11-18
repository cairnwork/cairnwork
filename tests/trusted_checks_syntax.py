"""Strict YAML subset for workflow files (Issue #1444).

Inside this subset the line structure the trusted-checks rules read is the structure YAML
parses, so line-based rules cannot be misled by alternative YAML spellings.
"""

from __future__ import annotations

import re


def _has_unclosed_quoted_scalar(line: str) -> bool:
    quoted = re.search(r":\s*(['\"])(.*)$", line)
    if not quoted:
        return False
    quote, tail = quoted.group(1), quoted.group(2)
    count = tail.count(quote) if quote == '"' else re.sub(r"''", "", tail).count("'")
    return count % 2 == 0


def _indentation_problem(
    name: str, number: int, indent: int, previous_indent: int | None, opens: bool
) -> str | None:
    if indent % 2:
        return f"{name}:{number}: strict YAML syntax requires two-space indentation"
    if previous_indent is not None and indent > previous_indent:
        if indent != previous_indent + 2 or not opens:
            return f"{name}:{number}: strict YAML syntax rejects this indentation"
    return None


def _commentless(line: str) -> str:
    return re.split(r"\s+#", line, maxsplit=1)[0]


def _inline_comment_allowed(line: str, subject: bool) -> bool:
    if subject and re.fullmatch(r" *-? *uses: +[^#\s]+@[0-9a-fA-F]{40} +# v[^\s]+", line):
        return True
    if not re.search(r"\s+#", line):
        return True
    value = _commentless(line)
    if not subject:
        if '"' not in value and "'" not in value:
            return True
        return bool(re.search(r":\s*['\"]", value)) and not _has_unclosed_quoted_scalar(value)
    return False


def _flat_flow_sequence(value: str) -> bool:
    item = r'(?:[A-Za-z0-9_.\-/]+|"[A-Za-z0-9_.\-/]+")'
    return re.fullmatch(rf"\[\s*{item}(?:\s*,\s*{item})*\s*\]", value) is not None


def _value_problems(
    name: str, number: int, line: str, indent: int, key: str, value: str, subject: bool
) -> tuple[list[str], int | None]:
    found: list[str] = []
    run_indent: int | None = None
    # Only three inert flow forms: an empty permissions map, and flat lists of plain
    # lowercase words under branches/types (e.g. `types: [opened, synchronize]`).
    flow_ok = (key == "permissions" and value == "{}") or (
        key in {"branches", "types"}
        and re.fullmatch(r"\[ ?[a-z_]+(?:, ?[a-z_]+)* ?\]", value) is not None
    )
    if not subject and value.startswith("[") and _flat_flow_sequence(value):
        flow_ok = True
    if value.startswith(("{", "[")) and not flow_ok:
        found.append(f"{name}:{number}: strict YAML syntax forbids flow values")
    if value.startswith(("&", "*", "!")) or re.search(
        r"(?<![A-Za-z0-9_-])[&*!][A-Za-z0-9_-]+", line
    ):
        found.append(f"{name}:{number}: strict YAML syntax forbids anchors, aliases, and tags")
    if value.startswith(("|", ">")):
        if key == "run" and value in {"|", ">"}:
            run_indent = indent + (2 if line[indent:].startswith("- ") else 0)
        else:
            found.append(f"{name}:{number}: strict YAML syntax allows block scalars only for run")
    return found, run_indent


def strict_syntax_problems(name: str, text: str, *, subject: bool = True) -> list[str]:
    """Reject YAML forms outside the deliberately small trusted-workflow subset."""
    found: list[str] = []
    run_indent: int | None = None
    previous_indent: int | None = None
    previous_opens = False
    first_structural = True
    mapping = re.compile(r"^( *)(?:- )?([A-Za-z0-9_-]+):( .*)?$")
    sequence = re.compile(r"^( *)- ([^\s#&*!|>'\"%@`{}\[\],?:][^#]*)$")
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line
        lowered = line.lower()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "input_" in lowered:
            found.append(f"{name}:{number}: strict YAML syntax forbids INPUT_")
        if first_structural and line.startswith(" "):
            found.append(f"{name}:{number}: first structural line must start at column 0")
        first_structural = False
        indent = len(line) - len(line.lstrip(" "))
        if run_indent is not None and indent > run_indent:
            continue
        run_indent = None
        if re.search(r"\s+#", line):
            if _inline_comment_allowed(line, subject):
                line = _commentless(line)
            else:
                found.append(f"{name}:{number}: strict YAML syntax forbids inline comments")
        found.extend(
            [problem]
            if (
                problem := _indentation_problem(
                    name, number, indent, previous_indent, previous_opens
                )
            )
            else []
        )
        match = mapping.fullmatch(line)
        item = sequence.fullmatch(line)
        if not match and not item:
            found.append(f"{name}:{number}: strict YAML syntax rejects this line")
            continue
        if re.match(r"^ *(?:- )?[?:]", line):
            found.append(f"{name}:{number}: strict YAML syntax rejects explicit keys")
        if "\\" in line:
            found.append(f"{name}:{number}: strict YAML syntax forbids backslashes")
        if "<<" in line:
            found.append(f"{name}:{number}: strict YAML syntax forbids merge keys")
        key = match.group(2).lower() if match else ""
        value = (
            match.group(3).strip()
            if match and match.group(3)
            else item.group(2).strip()
            if item
            else ""
        )
        problems, run_indent = _value_problems(name, number, line, indent, key, value, subject)
        found.extend(problems)
        if _has_unclosed_quoted_scalar(line):
            found.append(f"{name}:{number}: strict YAML syntax forbids multi-line quoted scalars")
        previous_indent = indent
        previous_opens = bool(
            (match and not match.group(3))
            or (item and re.match(r"[A-Za-z0-9_-]+:", item.group(2)))
            or (match and line[indent:].startswith("- "))
        )
    return found
