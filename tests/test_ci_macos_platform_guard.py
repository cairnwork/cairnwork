"""A pull request's macOS shards run only platform-specific test files; this guard keeps that
selection from silently losing one (Issue #1684). It runs on Linux, in the ordinary test job.

Three independent views of "platform-specific" must all be inside the selected set: pytest's own
collected sandbox / verifier_account marks, an AST read of each file (platform checks, seatbelt
names), and the text signal the selector itself uses (checked on a synthetic tree). The workflow
wiring is pinned too: the subset is for pull requests only, every other event runs everything.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import ci_macos_platform as platform_select
from scripts import ci_macos_shard as shards

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
CHECK_CALLS = {"sys.platform", "platform.system", "platform.uname", "os.uname"}
NAME_HINT = re.compile(r"seatbelt|sandbox-exec|sandbox_exec|darwin", re.IGNORECASE)
MARKS = {"sandbox", "verifier_account"}
# a test that skips at run time may be skipping one platform's case (Issue #1684 review)
SKIP_CALL = re.compile(r"(^|\.)(skip|importorskip)$")
# src modules with a platform branch. Their unmarked tests run on Linux in a pull request and on
# macOS in the nightly: an accepted trade-off. A new module here must be added on purpose.
SRC_PLATFORM_MODULES = {
    "src/cairnwork/account_checkout.py",
    "src/cairnwork/bytecode_hook.py",
    "src/cairnwork/confined.py",
    "src/cairnwork/conftest_reach.py",
    "src/cairnwork/old_python_probe.py",
    "src/cairnwork/proc_cwd.py",
    "src/cairnwork/proc_start.py",
    "src/cairnwork/protection.py",
    "src/cairnwork/run_scrub.py",
    "src/cairnwork/survivor_argv.py",
    "src/cairnwork/verifier_account.py",
    "src/cairnwork/verifier_escape.py",
    "src/cairnwork/verifier_select.py",
    "src/cairnwork_sandbox/__init__.py",
}
PLATFORM_IN_SRC = re.compile(r"sys\.platform|platform\.system|os\.uname|darwin", re.IGNORECASE)
_PR_PUSH = "github.event_name == 'pull_request' || github.event_name == 'push'"
_MAIN_DISPATCH = "(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main')"
_MACOS = (
    f"github.event_name == 'pull_request' || github.event_name == 'schedule' || {_MAIN_DISPATCH}"
)
# Every job condition is pinned exactly: an extra "or" branch must turn the guard red. The report
# job fires on 'failure' only: a run cancelled by a newer run is not a red run, a timeout is a
# failure (accepted trade-off; a cancel with no replacement leaves no Issue).
EXPECTED_IF = {
    "test": _PR_PUSH,
    "test-linux-sandbox": _PR_PUSH,
    "test-linux-verifier-account": _PR_PUSH,
    "demo-linux": _PR_PUSH,
    "test-macos-shard": "${{ " + _MACOS + " }}",
    "test-macos": "${{ always() && (" + _MACOS + ") }}",
    "macos-nightly-report": (
        "${{ always() && (github.event_name == 'schedule' || " + _MAIN_DISPATCH + ")"
        " && needs.test-macos-shard.result == 'failure' }}"
    ),
}


def selected() -> list[str]:
    return platform_select.platform_files(ROOT, shards.test_files(ROOT))


def ast_platform_files() -> set[str]:
    """Files whose syntax tree uses a platform check, a seatbelt name or a platform mark."""
    found = set()
    for rel in shards.test_files(ROOT):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                chain = ast.unparse(node)
                if chain in CHECK_CALLS or (
                    chain.startswith("pytest.mark.") and node.attr in MARKS
                ):
                    found.add(rel)
            elif isinstance(node, ast.Call) and SKIP_CALL.search(ast.unparse(node.func)):
                found.add(rel)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if NAME_HINT.search(node.value):
                    found.add(rel)
            elif isinstance(node, ast.Name | ast.alias) and NAME_HINT.search(
                node.id if isinstance(node, ast.Name) else node.name
            ):
                found.add(rel)
    return found


def collected_marked_files() -> set[str]:
    """Files holding a test pytest itself collects under the sandbox / verifier_account marks."""
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "CAIRNWORK_REQUIRE_"))
    }
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + ["-m", "sandbox or verifier_account", "tests"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    ).stdout
    return {line.split("::", 1)[0] for line in out.splitlines() if "::" in line}


def wiring_problems(text: str) -> list[str]:
    """What is wrong with how ci.yml hands the platform-only mode to the macOS shards."""
    problems = []
    if text.count("--platform-only") < 1 or "ci_macos_shard.py" not in text:
        problems.append("the shard step does not pass --platform-only")
    guard = re.search(r'\[ "\$EVENT" = "pull_request" \] && mode=\(--platform-only\)', text)
    if not guard:
        problems.append("--platform-only is not tied to the pull_request event alone")
    if len(re.findall(r"mode=\(--platform-only\)", text)) != 1:
        problems.append("--platform-only is set in more than one place")
    if "\n      - name: Verifier-account tests" in text and re.search(
        r"Verifier-account tests.*?--platform-only", text.split("# The required check")[0], re.S
    ):
        problems.append("the verifier-account step must not depend on the mode")
    for needle in (
        "  test-macos-shard:",
        "  test-macos:",
        "name: test-macos-shard (${{ matrix.shard }})",
        'shard: ["1", "2"]',
        "    needs: test-macos-shard\n    if: ${{ always() &&",
    ):
        if needle not in text:
            problems.append(f"a required check name or shape changed: {needle}")
    if "\n  workflow_dispatch:\n" not in text or "\n  schedule:\n" not in text:
        problems.append("the full suite lost its nightly or manual trigger")
    problems += report_problems(text) + trigger_problems(text)
    feed = 'ci_macos_shard.py "${{ matrix.shard }}" ${mode[@]+"${mode[@]}"})'
    if feed not in text or '"${files[@]}"' not in text:
        problems.append("the selector output no longer feeds the pytest file list")
    return problems


def job_block(text: str, name: str) -> str:
    found = re.search(rf"\n  {re.escape(name)}:\n(.*?)(?=\n  [A-Za-z][\w-]*:\n|\Z)", text, re.S)
    return "\n" + found.group(1) if found else ""


def job_if(job: str) -> str:
    condition = re.search(r"\n    if: (.*)", job)
    return condition.group(1).strip() if condition else ""


def report_problems(text: str) -> list[str]:
    job = job_block(text, "macos-nightly-report")
    problems = []
    if not job:
        return ["a red full macOS run no longer opens or updates an Issue"]
    if job_if(job) != EXPECTED_IF["macos-nightly-report"]:
        problems.append("the report job's if is not exactly: a red full run on schedule or main")
    perms = re.search(r"\n    permissions:\n((?:      .*\n)+)", job)
    if not perms or {ln.strip() for ln in perms.group(1).splitlines()} != {
        "contents: read",
        "issues: write",
    }:
        problems.append("the report job needs exactly contents: read and issues: write")
    if "needs: test-macos-shard" not in job or "macOS nightly failed" not in job:
        problems.append("the report job is not tied to the macOS shards or its Issue title")
    return problems


def trigger_problems(text: str) -> list[str]:
    """A manual or scheduled run starts neither the Linux jobs nor a non-main macOS run."""
    problems = []
    for name, expected in EXPECTED_IF.items():
        if name != "macos-nightly-report" and job_if(job_block(text, name)) != expected:
            problems.append(f"job {name}: its if is not exactly the pinned expression")
    return problems


def test_every_marked_test_file_pytest_collects_is_in_the_pull_request_set():
    marked = collected_marked_files()
    assert marked, "the collection found no sandbox or verifier_account test: the probe is broken"
    assert marked <= set(selected()), sorted(marked - set(selected()))


def test_every_file_with_a_platform_check_or_seatbelt_name_in_its_syntax_is_in_the_set():
    seen = ast_platform_files()
    assert seen, "the syntax read found nothing: the probe is broken"
    assert seen <= set(selected()), sorted(seen - set(selected()))


def test_the_pull_request_set_is_a_proper_subset_of_the_suite():
    chosen, everything = set(selected()), set(shards.test_files(ROOT))
    assert chosen < everything
    assert shards.WITNESS_FILE not in chosen


@pytest.mark.parametrize(
    "text",
    [
        "import pytest\n@pytest.mark.sandbox\ndef test_a(): ...\n",
        "import pytest\npytestmark = pytest.mark.verifier_account\n",
        "import sys\nX = sys.platform\n",
        "import platform\nplatform.system()\n",
        "from cairnwork_sandbox import seatbelt\n",
        "S = '/usr/bin/sandbox-exec'\n",
        "import pytest\n@pytest.mark.skipif(True, reason='x')\ndef test_a(): ...\n",
        "import os\nos.environ['CAIRNWORK_REQUIRE_SANDBOX']\n",
        "from cairnwork.verifier_account import VerifierAccount\n",
    ],
)
def test_a_new_test_file_with_a_platform_signal_is_selected(tmp_path, text):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text(text)
    paths = shards.test_files(tmp_path)
    assert platform_select.platform_files(tmp_path, paths) == ["tests/test_new.py"]


@pytest.mark.parametrize(
    "text",
    [
        "import pytest\ndef test_a():\n    pytest.skip('no')\n",
        "import pytest\nx = pytest.importorskip('y')\n",
        "import pytest\n@pytest.mark.skip(reason='x')\ndef test_a(): ...\n",
    ],
)
def test_a_test_that_skips_at_run_time_is_selected(tmp_path, text):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text(text)
    assert platform_select.platform_files(tmp_path, shards.test_files(tmp_path)) == [
        "tests/test_new.py"
    ]


def test_the_src_modules_with_a_platform_branch_are_the_listed_ones():
    found = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src").rglob("*.py")
        if PLATFORM_IN_SRC.search(p.read_text(encoding="utf-8"))
    }
    assert found == SRC_PLATFORM_MODULES, sorted(found ^ SRC_PLATFORM_MODULES)


def test_a_plain_test_file_is_left_to_the_linux_jobs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_plain.py").write_text("def test_a():\n    assert 1 + 1 == 2\n")
    assert platform_select.platform_files(tmp_path, shards.test_files(tmp_path)) == []


def test_an_unreadable_file_counts_as_platform_specific(tmp_path):
    assert platform_select.is_platform_specific(tmp_path / "missing.py") is True


def run_entry(shard: str, *extra: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_macos_shard.py"), shard, *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    return out.splitlines()


def test_the_real_entry_splits_the_pull_request_set_across_the_two_shards_exactly_once():
    one, two = run_entry("1", "--platform-only"), run_entry("2", "--platform-only")
    assert sorted(one + two) == sorted(selected())
    assert not set(one) & set(two)
    assert one and two


def test_the_real_entry_without_the_flag_still_runs_every_file_exactly_once():
    one, two = run_entry("1"), run_entry("2")
    assert sorted(one + two) == shards.test_files(ROOT)


def test_the_entry_refuses_an_unknown_flag_instead_of_running_the_whole_suite_silently():
    bad = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci_macos_shard.py"), "1", "--platform"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bad.returncode != 0 and "usage" in bad.stderr


def test_ci_yml_gives_the_subset_to_pull_requests_only_and_keeps_the_check_names():
    assert wiring_problems(CI) == []


@pytest.mark.parametrize(
    "old, new",
    [
        ('[ "$EVENT" = "pull_request" ] && mode=(--platform-only)', "mode=(--platform-only)"),
        ('[ "$EVENT" = "pull_request" ] && mode=(--platform-only)', "mode=()"),
        ("  test-macos-shard:", "  test-macos-shard-pr:"),
        ('shard: ["1", "2"]', 'shard: ["1"]'),
        ("  workflow_dispatch:\n", ""),
        ("  macos-nightly-report:", "  macos-nightly-report-off:"),
        (
            "needs.test-macos-shard.result == 'failure'",
            "needs.test-macos-shard.result == 'success'",
        ),
        ("      issues: write\n    steps:\n      - name: Open", "    steps:\n      - name: Open"),
        (
            "      contents: read\n      issues: write\n    steps:\n      - name: Open",
            "      contents: write\n      issues: write\n    steps:\n      - name: Open",
        ),
        (
            "github.event_name == 'pull_request' || github.event_name == 'push'",
            "github.event_name != 'schedule'",
        ),
        (
            "(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') }}\n"
            "    strategy:",
            "github.event_name == 'workflow_dispatch' }}\n    strategy:",
        ),
        (
            'ci_macos_shard.py "${{ matrix.shard }}" ${mode[@]+"${mode[@]}"})',
            'ci_macos_shard.py "${{ matrix.shard }}")',
        ),
    ],
)
def test_the_wiring_check_goes_red_when_the_workflow_is_weakened(old, new):
    assert old in CI
    assert wiring_problems(CI.replace(old, new, 1)) != []
