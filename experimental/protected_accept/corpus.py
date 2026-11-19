"""Small synthetic corpus for measuring protected acceptance."""

from __future__ import annotations

import json
from typing import Any

from cairnwork.blackbox import parse

from .corpus_tamper import tamper_sources

TASKS: dict[str, tuple[str, str, str, list[dict[str, Any]]]] = {
    "clamp": (
        "clamp",
        "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
        "clamp(8,0,5)",
        [
            {"args": [8, 0, 5], "expected": 5},
            {"args": [-2, 0, 5], "expected": 0},
            {"args": [3, 0, 5], "expected": 3},
            {"args": [7, 2, 9], "expected": 7},
        ],
    ),
    "slug": (
        "slug",
        "import re\ndef slug(text):\n    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n",  # noqa: E501
        "slug('Hello World!')",
        [
            {"args": ["Hello World!"], "expected": "hello-world"},
            {"args": [" A__B "], "expected": "a-b"},
            {"args": ["one"], "expected": "one"},
        ],
    ),
    "median": (
        "median",
        "def median(xs):\n    ys=sorted(xs); n=len(ys)\n    if not n: raise ValueError('empty sequence')\n    m=n//2\n    return ys[m] if n%2 else (ys[m-1]+ys[m])/2\n",  # noqa: E501
        "median([1,9,3])",
        [
            {"args": [[1, 9, 3]], "expected": 3},
            {"args": [[1, 4, 7, 9]], "expected": 5.5, "tolerance": {"abs_tol": 0.0}},
            {"args": [[-2, 6, 1]], "expected": 1},
            {"args": [[]], "raises": {"type": "ValueError", "message": "empty sequence"}},
        ],
    ),
    "duration": (
        "duration",
        "import re\ndef parse_duration(text):\n    parts=re.findall(r'(\\d+)([hms])',text)\n    if not parts or ''.join(n+u for n,u in parts)!=text: raise ValueError('invalid duration')\n    return sum(int(n)*{'h':3600,'m':60,'s':1}[u] for n,u in parts)\n",  # noqa: E501
        "parse_duration('1h30m')",
        [
            {"args": ["1h30m"], "expected": 5400},
            {"args": ["45s"], "expected": 45},
            {"args": ["2h3m4s"], "expected": 7384},
            {"args": ["bad"], "raises": {"type": "ValueError", "message": "invalid duration"}},
        ],
    ),
    "rle": (
        "rle",
        "def rle(text):\n    out=[]\n    for ch in text:\n        if out and out[-1][0]==ch: out[-1][1]+=1\n        else: out.append([ch,1])\n    return out\n",  # noqa: E501
        "rle('aab')",
        [
            {"args": ["aab"], "expected": [["a", 2], ["b", 1]]},
            {"args": [""], "expected": []},
            {"args": ["xyz"], "expected": [["x", 1], ["y", 1], ["z", 1]]},
        ],
    ),
    "counter": (
        "counter",
        "class Counter:\n    def __init__(self): self.value=0\n    def inc(self, amount=1): self.value+=amount; return self.value\n    def total(self): return self.value\n",  # noqa: E501
        "Counter",
        [
            {
                "id": "sequence",
                "init": {"args": []},
                "calls": [
                    {"method": "inc", "args": [], "expected": 1},
                    {"method": "inc", "args": [3], "expected": 4},
                    {"method": "total", "args": [], "expected": 4},
                ],
            },
            {
                "id": "negative",
                "init": {"args": []},
                "calls": [
                    {"method": "inc", "args": [-2], "expected": -2},
                    {"method": "total", "args": [], "expected": -2},
                ],
            },
        ],
    ),
}


FUNCTIONS = {"duration": "parse_duration"}  # the declared function where it differs from the task


def declaration(task: str, *, low_entropy: bool = False, boolean_low: bool = False):
    module, _, _, cases = TASKS[task]
    copied = [
        case if task == "counter" else {"id": f"case-{i}", **case}
        for i, case in enumerate(cases, 1)
    ]
    check: dict[str, Any] = (
        {"id": task, "module": module, "class": "Counter", "cases": copied}
        if task == "counter"
        else {"id": task, "module": module, "function": FUNCTIONS.get(task, task), "cases": copied}
    )
    if low_entropy:
        check.update(
            function=FUNCTIONS.get(task, task),
            cases=[
                {"id": "guess", "args": [0], "expected": True}
                if boolean_low
                else {"id": "guess", "args": [0, 0, 0], "expected": 0}
            ],
        )
        check.pop("class", None)
    return parse(json.dumps({"salt": "a1" * 32, "checks": [check]}).encode())


def _honest(task: str, index: int) -> str:
    variants = {
        "clamp": [
            TASKS[task][1],
            "def clamp(x,lo,hi):\n    if x < lo: return lo\n    if x > hi: return hi\n    return x\n",  # noqa: E501
            "def clamp(x,lo,hi): return lo if x<lo else hi if x>hi else x\n",
        ],
        "slug": [
            TASKS[task][1],
            "import re\ndef slug(text): return '-'.join(p for p in re.split(r'[^a-z0-9]+',text.lower()) if p)\n",  # noqa: E501
            "def slug(text):\n    out=[]; dash=False\n    for ch in text.lower():\n"
            "        if 'a'<=ch<='z' or '0'<=ch<='9':\n"
            "            if dash and out: out.append('-')\n"
            "            out.append(ch); dash=False\n        else: dash=True\n"
            "    return ''.join(out)\n",
        ],
        "median": [
            TASKS[task][1],
            "from statistics import median as med\ndef median(xs):\n    if not xs: raise ValueError('empty sequence')\n    return med(xs)\n",  # noqa: E501
            "def median(xs):\n    if not xs: raise ValueError('empty sequence')\n"
            "    ordered=sorted(xs); middle=len(ordered)//2\n"
            "    return ordered[middle] if len(ordered)%2 else "
            "(ordered[middle-1]+ordered[middle])/2\n",
        ],
        "duration": [
            TASKS[task][1],
            "def parse_duration(text):\n    total=0; number=''; units={'h':3600,'m':60,'s':1}\n    for ch in text:\n        if ch.isdigit(): number+=ch\n        elif ch in units and number: total+=int(number)*units[ch]; number=''\n        else: raise ValueError('invalid duration')\n    if number or not text: raise ValueError('invalid duration')\n    return total\n",  # noqa: E501
            "def parse_duration(text):\n    units={'h':3600,'m':60,'s':1}; total=0; pos=0\n    while pos < len(text):\n        end=pos\n        while end<len(text) and text[end].isdigit(): end+=1\n        if end==pos or end==len(text) or text[end] not in units: raise ValueError('invalid duration')\n        total+=int(text[pos:end])*units[text[end]]; pos=end+1\n    if not text: raise ValueError('invalid duration')\n    return total\n",  # noqa: E501
        ],
        "rle": [
            TASKS[task][1],
            "from itertools import groupby\ndef rle(text): return [[c,len(list(g))] for c,g in groupby(text)]\n",  # noqa: E501
        ],
        "counter": [
            TASKS[task][1],
            "class Counter:\n    def __init__(self): self.value=0\n    def inc(self, amount=1):\n        self.value=self.value+amount\n        return self.value\n    def total(self): return self.value\n",  # noqa: E501
        ],
    }
    return variants[task][min(index, len(variants[task]) - 1)]


def _files(task: str, source: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    module, _, expr, _ = TASKS[task]
    expected = {
        "clamp": "5",
        "slug": "'hello-world'",
        "median": "3",
        "duration": "5400",
        "rle": "[['a', 2], ['b', 1]]",
        "counter": "None",
    }[task]
    if task == "counter":
        test = "from counter import Counter\ndef test_visible():\n c=Counter(); assert c.inc()==1; assert c.inc(3)==4; assert c.total()==4\n"  # noqa: E501
    else:
        name = FUNCTIONS.get(task, task)
        test = f"from {module} import {name}\ndef test_visible(): assert {expr} == {expected}\n"
    files = {
        f"src/{module}.py": source,
        "tests/test_visible.py": test,
        "tests/conftest.py": "import pathlib,sys\nsys.path.insert(0,str(pathlib.Path(__file__).parent.parent/'src'))\n",  # noqa: E501
    }
    files.update(extra or {})
    return files


def build_corpus() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for task in TASKS:
        indices = range(3) if task in {"clamp", "duration", "slug", "median"} else range(2)
        for index in indices:
            source = _honest(task, index)
            entries.append(
                {
                    "id": f"{task}-honest-{index}",
                    "task": task,
                    "kind": "honest",
                    "cls": "",
                    "variant": index,
                    "files": _files(task, source),
                    "source": source,
                    "expected_source": TASKS[task][1],
                    "expect_protected": "PROTECTED_PASS",
                    "declaration": declaration(task),
                    "known_residual": False,
                }
            )
    for item in tamper_sources():
        task = item["task"]
        decl = declaration(task, low_entropy=item["low"], boolean_low=item["boolean_low"])
        entries.append(
            {
                "id": f"{task}-{item['cls']}-{item['variant']}",
                "task": task,
                "kind": "tamper",
                "cls": item["cls"],
                "variant": item["variant"],
                "files": _files(task, item["source"], item["extra"]),
                "source": item["source"],
                "expected_source": TASKS[task][1],
                "expect_protected": "not PASS",
                "declaration": decl,
                "known_residual": item["known_residual"],
                "reuse_lock": item["reuse_lock"],
                "resalt": item["resalt"],
                "timeout": item["timeout"],
            }
        )
    return entries


ENTRIES = build_corpus()
