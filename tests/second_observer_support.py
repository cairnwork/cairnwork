"""Build a tiny git project (base, test commit, candidates) and a shadow input for it."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from cairnwork import pytest_config
from cairnwork.deferred_tests import lay_tests_over
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext
from experimental.second_observer.cli import main
from experimental.second_observer.inputs import describe_verifier

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TESTS = {
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
VERIFIER_CHECKOUT = "/verifier/checkout"  # where the verifier's own run happened (ini text only)


def git(repo, *args):
    ident = ("-c", "user.name=t", "-c", "user.email=t@t")
    done = subprocess.run(
        ["git", *ident, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


class Project:
    def __init__(self, root: Path, tests=None, base_files=None):
        self.root = root.resolve()
        self.repo = self.root / "project"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        self.base = self.commit({"src/calc.py": BUGGY, **(base_files or {})}, "base")
        self.tests = tests or TESTS
        self.test_commit = self.commit(self.tests, "tests", parent=self.base)

    def commit(self, files: dict, message: str, parent=None) -> str:
        if parent:
            git(self.repo, "checkout", "-q", "--detach", parent)
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        sha = git(self.repo, "rev-parse", "HEAD")
        return sha

    def candidate(self, files: dict) -> str:
        return self.commit(files, "candidate", parent=self.base)

    def tested(self, candidate: str) -> str:
        paths = tuple(sorted(self.tests))
        return lay_tests_over(self.repo, candidate, self.test_commit, paths, "t", self.root)

    def tree(self, candidate: str) -> str:
        return git(self.repo, "rev-parse", f"{self.tested(candidate)}^{{tree}}")

    def cw_verdict(self, candidate: str, target: str) -> str:
        """The real Cairnwork verifier run on the same tested commit: PASS or FAIL."""
        ctx = VerifyContext(
            self.repo, self.root / "cw", base_commit=self.base, import_paths=("src",)
        )
        spec = parse_verifier("v", ["python3", "-m", "pytest", target.split("::")[0]])
        run = run_verifiers(ctx, self.tested(candidate), (spec,), "cw")[0]
        return "PASS" if run.returncode == 0 and not run.why else "FAIL"

    def shadow_input(self, candidate: str, target: str, **over) -> dict:
        settings = pytest_config.verifier_settings(self.repo, self.base, ("src",))
        plugins = pytest_config.installed_plugins(sys.executable)
        ini = settings.ini_text(Path(VERIFIER_CHECKOUT), plugins)
        raw = {
            "repo": str(self.repo),
            "base": self.base,
            "candidate": candidate,
            "test_commit": self.test_commit,
            "tested_tree": self.tree(candidate),
            "target": target,
            "cw_verdict": "",
            **describe_verifier(sys.executable, ini),
        }
        raw.update(over)
        return {k: v for k, v in raw.items() if v is not None}

    def shadow(self, raw: dict, name=None, protect=()) -> tuple[int, Any, Path]:
        """Run the CLI on ``raw``; return (exit code, the one JSON row or None, the out dir)."""
        self.runs = getattr(self, "runs", 0) + 1
        name = name or f"out{self.runs}"
        source, out = self.root / f"{name}.json", self.root / name
        source.write_text(json.dumps(raw))
        args = ["--input", str(source), "--out", str(out)]
        for path in protect:
            args += ["--protect", str(path)]
        code = main(args)
        rows = (out / "shadow.jsonl").read_text().splitlines() if out.is_dir() else []
        return code, (json.loads(rows[0]) if rows else None), out
