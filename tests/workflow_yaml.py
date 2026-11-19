"""Minimal parser for the YAML subset the workflow files use (no YAML dependency installed)."""

from __future__ import annotations

import re
from typing import Any


def _scalar(text: str) -> Any:
    text = text.strip()
    quoted = re.match(r"""^(["'])(.*?)\1(\s+#.*)?$""", text)
    if quoted:
        return quoted.group(2)
    text = re.sub(r"\s+#.*$", "", text)
    if text == "{}":
        return {}
    if text.startswith("[") and text.endswith("]"):
        return [part.strip().strip("'\"") for part in text[1:-1].split(",") if part.strip()]
    return text


def parse_yaml(text: str) -> Any:
    lines = text.splitlines()
    pos = 0

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def skip() -> None:
        nonlocal pos
        while pos < len(lines) and (not lines[pos].strip() or lines[pos].lstrip().startswith("#")):
            pos += 1

    def block(indent: int) -> Any:
        skip()
        return seq(indent) if lines[pos].lstrip().startswith("- ") else mapping(indent)

    def seq(indent: int) -> list[Any]:
        nonlocal pos
        items: list[Any] = []
        while True:
            skip()
            if pos >= len(lines) or indent_of(lines[pos]) != indent:
                return items
            if not lines[pos].lstrip().startswith("- "):
                return items
            rest = lines[pos].lstrip()[2:]
            if re.match(r"^[\w.-]+:(\s|$)", rest):
                lines[pos] = " " * (indent + 2) + rest
                items.append(mapping(indent + 2))
            else:
                pos += 1
                items.append(_scalar(rest))

    def mapping(indent: int) -> dict[str, Any]:
        nonlocal pos
        result: dict[str, Any] = {}
        while True:
            skip()
            if pos >= len(lines) or indent_of(lines[pos]) != indent:
                return result
            found = re.match(r"^([\w.-]+):(?:\s+(.*))?$", lines[pos].strip())
            assert found, f"unsupported line: {lines[pos]!r}"
            key, value = found.group(1), found.group(2) or ""
            pos += 1
            if value in {"|", ">"}:
                body: list[str] = []
                while pos < len(lines) and (
                    not lines[pos].strip() or indent_of(lines[pos]) > indent
                ):
                    body.append(lines[pos].strip())
                    pos += 1
                result[key] = "\n".join(body)
            elif value:
                result[key] = _scalar(value)
            else:
                skip()
                nested = pos < len(lines) and (
                    indent_of(lines[pos]) > indent
                    or (indent_of(lines[pos]) == indent and lines[pos].lstrip().startswith("- "))
                )
                result[key] = block(indent_of(lines[pos])) if nested else None

    return block(0)
