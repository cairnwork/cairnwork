"""Writes pause_secret_1886_main_blocked.json: the corpus inputs that the scanner at main
3e3097bd refuses. Run from the repo root: python tests/gen_pause_secret_1886_main.py"""

import json
import pathlib
import subprocess
import sys
import types

sys.path.insert(0, "tests")
import test_pause_secret_schemes_1886 as t  # noqa: E402

src = subprocess.run(
    ["git", "show", "3e3097bd:experimental/pause/secrets_scan.py"],
    capture_output=True,
    text=True,
    check=True,
    timeout=30,
).stdout
main = types.ModuleType("main_scan")
exec(compile(src, "main_scan", "exec"), main.__dict__)
corpus = t.differential_corpus() + t._REVIEW_MUST_BLOCK + t._REVIEW_MUST_PASS
blocked = [x for x in dict.fromkeys(corpus) if main.scan(x)]
out = pathlib.Path("tests/pause_secret_1886_main_blocked.json")
out.write_text(json.dumps({"source": "3e3097bd", "blocked": blocked}), encoding="utf-8")
print(len(blocked), "blocked of", len(set(corpus)))
