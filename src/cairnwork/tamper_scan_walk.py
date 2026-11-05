"""Hook names bound by changed ``.py`` blobs, read by the verifier's own interpreter (#821, #848b).

Run as a program (``python -I -S -B -c <this file> <names json>``), never imported by the
scanner: tamper_scan_plugin.py reads this file and hands it to the interpreter that will import
the candidate, so the grammar and the name rules are the importer's. stdlib only, and no syntax
newer than the oldest Python a target may use. Input on stdin: a 4-byte big-endian count, then
each blob as a 4-byte length and its bytes. Output: one JSON object ``{"rows": [[kind, names]]}``,
one row per blob in order. ``kind`` says how far the blob got, never an error text:

* ``ok``: parsed; ``names`` are the hook names it binds or holds as a whole string;
* ``syntax``: the last step, ``ast.parse`` of the bytes, raised ``SyntaxError`` (a fixed grammar
  limit or an invalid token, the same in every runtime state);
* ``fail``: anything else, including a bad coding cookie, an unknown codec, bytes that do not
  decode, ``RecursionError`` and ``MemoryError`` (these depend on the importing process's state).
"""

from __future__ import annotations

import ast
import codecs
import io
import json
import sys
import tokenize

_NOT_NAMES = frozenset({"type_comment", "kind"})  # str fields that are not identifiers


def hook_names_bound(source, wanted=None):
    """Names the module binds or writes as a whole string; a parse failure is raised.

    Every str (or list of str) field of every node counts, whatever the grammar calls it: a name,
    a parameter (``arg``), a keyword, an alias, ``global``/``nonlocal`` names, a type parameter,
    a ``match`` capture, an attribute, and a string or bytes literal. A new binding form in a later
    Python is read without a change here (tests/test_tamper_scan_plugin_fields.py walks ``ast``).
    Identifiers come from the importer's own parser, so they are already NFKC (PEP 3131); string
    values are not normalised, since Python does not. Only ``alias.name`` and ``ImportFrom.module``
    are split on "." (``import a.b``).
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        dotted = isinstance(node, (ast.alias, ast.ImportFrom))
        for field in node._fields:
            value = getattr(node, field, None)
            if field in _NOT_NAMES:
                continue
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            for text in value if isinstance(value, list) else (value,):
                if isinstance(text, str):
                    found.add(text)
                    if dotted and field in ("name", "module"):
                        found.update(text.split("."))
    return found if wanted is None else found & wanted


def read_blob(source, wanted):
    """``[kind, sorted names]`` for one blob, steps in order: cookie, codec, decode, parse."""
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(source).readline)[0]
        codecs.lookup(encoding)
        source.decode(encoding)
    except BaseException:  # noqa: BLE001 - fail closed on anything before the parse
        return ["fail", []]
    try:
        tree_names = hook_names_bound(source, wanted)
    except SyntaxError:
        return ["syntax", []]
    except BaseException:  # noqa: BLE001 - RecursionError, MemoryError, anything else
        return ["fail", []]
    return ["ok", sorted(tree_names)]


def _take(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("short input")
    return data


def main():
    wanted = frozenset(json.loads(sys.argv[1]))
    stream = sys.stdin.buffer
    count = int.from_bytes(_take(stream, 4), "big")
    rows = []
    for _ in range(count):
        size = int.from_bytes(_take(stream, 4), "big")
        rows.append(read_blob(_take(stream, size), wanted))
    sys.stdout.write(json.dumps({"rows": rows}))


if __name__ == "__main__":
    main()
