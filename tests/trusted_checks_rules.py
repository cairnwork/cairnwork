"""Static rules for the trusted-checks boundary (used by tests/test_trusted_checks_app.py)."""

from __future__ import annotations

import re
from pathlib import Path

from tests.trusted_checks_syntax import _commentless, strict_syntax_problems

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ENV_NAME = "trusted-checks"
# Triggers whose GITHUB_REF is the default branch (pull_request_target: base branch, which the
# workflows restrict to main). pull_request, push, workflow_dispatch, schedule etc. are excluded.
MAIN_ONLY_TRIGGERS = {"pull_request_target", "issue_comment", "workflow_run"}
APP_TOKEN_ACTION = re.compile(
    r"['\"]?uses['\"]?:\s*['\"]?actions/create-github-app-token@([0-9a-f]{40})",
    re.IGNORECASE,
)
APP_TOKEN_SHA = "64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
# The App token may carry exactly this and nothing else (non-empty: an empty set inherits all).
ALLOWED_APP_PERMISSIONS = {("permission-checks", "write")}
EXPECTED = {
    "pyright-trusted.yml": "pyright (trusted)",
    "pin-guard-trusted.yml": "pin guard (trusted)",
}
SUBJECT_MARKERS = (
    ENV_NAME,
    "trusted_checks_",
    "create-github-app-token",
    APP_TOKEN_SHA,
)


def _is_subject(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in SUBJECT_MARKERS)


def _top_level_block(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if re.match(rf"^['\"]?{re.escape(key)}['\"]?:", line):
            inside = True
            rest = line.split(":", 1)[1].strip()
            if rest:
                out.append(rest)
            continue
        if inside:
            if line and not line[0].isspace() and not line.startswith("#"):
                break
            out.append(line)
    return out


def triggers(text: str) -> set[str]:
    block = _top_level_block(text, "on")
    if block and not block[0].startswith(" "):
        inline = block[0].strip("[] ")
        return {part.strip() for part in inline.split(",") if part.strip()}
    found = set()
    for line in block:
        match = re.match(r"^  ([A-Za-z_]+):", line)
        if match:
            found.add(match.group(1))
    return found


def jobs(text: str) -> dict[str, str]:
    block = _top_level_block(text, "jobs")
    result: dict[str, list[str]] = {}
    current = None
    for line in block:
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(body) for name, body in result.items()}


def uses_trusted_env(job: str) -> bool:
    return ENV_NAME in job.lower()


def app_token_permissions(job: str) -> list[set[tuple[str, str]]]:
    """The permission-* inputs of every create-github-app-token step, one set per step."""
    lines = job.splitlines()
    result: list[set[tuple[str, str]]] = []
    for index, raw_line in enumerate(lines):
        line = _commentless(raw_line)
        if not _is_token_reference(line) or line.lstrip().startswith("#"):
            continue
        dash = line.find("- ")
        indent = dash if dash >= 0 and line[:dash].strip() == "" else None
        body = [line]
        for follow in lines[index + 1 :]:
            if indent is not None and re.match(rf"^ {{{indent}}}- ", follow):
                break
            body.append(follow)
        step = "\n".join(body)
        result.append(
            set(
                re.findall(
                    r"['\"]?(permission-[a-z-]+)['\"]?\s*:\s*['\"]?([^,}\s'\"]+)",
                    step,
                    re.I,
                )
            )
        )
    return result


def _is_token_reference(text: str) -> bool:
    return "create-github-app-token" in text.lower() or APP_TOKEN_SHA in text.lower()


def _whole_secrets_reference(line: str) -> bool:
    """Outside comment lines, `secrets` may only appear as secrets.NAME."""
    if re.match(r"^\s*#", line):
        return False
    return bool(re.search(r"\bsecrets\b(?!\s*\.\s*[A-Za-z_])", line, re.I))


def _environment_problems(name: str, text: str) -> list[str]:
    found: list[str] = []
    lines = text.splitlines()
    for index, raw_line in enumerate(lines):
        line = _commentless(raw_line)
        if re.search(r"\b(?:secrets|vars)\s*\[", raw_line, re.I):
            found.append(f"{name}:{index + 1}: indexed secrets or vars references are forbidden")
        if _whole_secrets_reference(raw_line):
            found.append(f"{name}:{index + 1}: whole secrets object references are forbidden")
        match = re.match(r"^( *)environment\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2)
        if value:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                found.append(f"{name}:{index + 1}: environment value must be a plain word")
            if re.search(r"\bformat\s*\(", value, re.I):
                found.append(f"{name}:{index + 1}: format() in environment value is forbidden")
            continue
        for child_index in range(index + 1, len(lines)):
            child = _commentless(lines[child_index])
            child_indent = len(child) - len(child.lstrip(" "))
            if child.strip() and child_indent <= indent:
                break
            child_match = re.match(r"^ +name\s*:\s*(.*?)\s*$", child)
            if child_match:
                child_value = child_match.group(1)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", child_value):
                    found.append(f"{name}:{child_index + 1}: environment name must be a plain word")
                if re.search(r"\bformat\s*\(", child_value, re.I):
                    found.append(
                        f"{name}:{child_index + 1}: format() in environment value is forbidden"
                    )
    return found


# A remote reusable workflow runs in this repository's context and may bind its own job to the
# trusted environment, which this checker cannot see. Each one is pinned here to a commit whose
# workflow was reviewed not to use the environment; bumping the pin needs a PR that edits this set.
REUSABLE_WORKFLOWS = frozenset(
    {
        "James3014/Nexus-new/.github/workflows/sibling-merge-checks.yml"
        "@c72f214c9b4865fbc1ed88a371ccf4c40562d41b",
    }
)


def _reusable_workflow_problems(name: str, text: str) -> list[str]:
    found: list[str] = []
    for index, line in enumerate(text.splitlines()):
        call = re.match(r"^ {4}uses\s*:\s*(\S+)\s*$", _commentless(line), re.I)
        if not call:
            continue
        target = call.group(1)
        local = re.fullmatch(r"\./\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", target)
        if not local and target not in REUSABLE_WORKFLOWS:
            found.append(f"{name}:{index + 1}: job calls an unreviewed remote reusable workflow")
    return found


def _parent_index(lines: list[str], index: int) -> int | None:
    indent = len(lines[index]) - len(lines[index].lstrip())
    for pos in range(index - 1, -1, -1):
        text = lines[pos]
        if text.strip() and not text.lstrip().startswith("#"):
            if len(text) - len(text.lstrip()) < indent:
                return pos
    return None


def _trusted_job_problems(name: str, job_name: str, job: str) -> list[str]:
    found: list[str] = []
    lines = job.splitlines()
    for index, line in enumerate(lines):
        if "TRUSTED_CHECKS_PRIVATE_KEY" not in line.upper():
            continue
        step_start = next(
            (pos for pos in range(index, -1, -1) if re.match(r"^\s*-\s", lines[pos])),
            None,
        )
        uses_line = next(
            (
                _commentless(lines[pos])
                for pos in range(step_start or 0, index + 1)
                if re.match(r"^\s*(?:-\s*)?uses\s*:", lines[pos], re.I)
            ),
            "",
        )
        token_step = bool(
            re.match(
                r"^\s*(?:-\s*)?uses:\s*actions/create-github-app-token@[0-9a-fA-F]{40}\s*$",
                uses_line,
                re.I,
            )
        )
        allowed_key = re.match(
            r"^\s*private-key:\s*\$\{\{\s*secrets\.TRUSTED_CHECKS_PRIVATE_KEY\s*}}\s*$",
            line,
            re.I,
        )
        parent = _parent_index(lines, index)
        under_with = (
            parent is not None
            and _commentless(lines[parent]).strip().lower() == "with:"
            and step_start is not None
            and _parent_index(lines, parent) == step_start
        )
        if not token_step or not allowed_key or not under_with:
            found.append(
                f"{name}:{job_name}: TRUSTED_CHECKS_PRIVATE_KEY outside the token private-key input"
            )
    for line in lines:
        use = re.match(r"^\s*(?:-\s*)?uses:\s*(\S+)\s*$", _commentless(line), re.I)
        if use and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[0-9a-fA-F]{40}",
            use.group(1),
        ):
            found.append(
                f"{name}:{job_name}: trusted environment job uses an unpinned or local action"
            )
    return found


def _key_appears_outside_trusted_job(text: str) -> bool:
    all_keys = len(re.findall(r"TRUSTED_CHECKS_PRIVATE_KEY", text, re.I))
    job_keys = sum(
        len(re.findall(r"TRUSTED_CHECKS_PRIVATE_KEY", body, re.I))
        for body in jobs(text).values()
        if uses_trusted_env(body)
    )
    return all_keys > job_keys


def problems(name: str, text: str) -> list[str]:
    """Every rule violation in one workflow file, as short messages."""
    subject = _is_subject(text)
    found = strict_syntax_problems(name, text, subject=subject)
    found.extend(_environment_problems(name, text))
    found.extend(_reusable_workflow_problems(name, text))
    lowered = text.lower()
    mentions = (
        ENV_NAME in lowered
        or "trusted_checks_" in lowered
        or _is_token_reference(text)
        or bool(re.search(r"TRUSTED_CHECKS_PRIVATE_KEY", text, re.I))
    )
    if not mentions:
        return found
    if _key_appears_outside_trusted_job(text):
        found.append(
            f"{name}: TRUSTED_CHECKS_PRIVATE_KEY appears outside a trusted environment job"
        )
    extra = triggers(text) - MAIN_ONLY_TRIGGERS
    if extra:
        found.append(f"{name}: references {ENV_NAME} but has triggers {sorted(extra)}")
    if re.search(r"&[A-Za-z0-9_-]+|\*[A-Za-z0-9_-]+|(?:^|\s)<<\s*:", text):
        found.append(f"{name}: YAML anchors or aliases may hide trusted token configuration")
    for job_name, job in jobs(text).items():
        in_env = uses_trusted_env(job)
        if "TRUSTED_CHECKS_" in job and not in_env:
            found.append(f"{name}:{job_name}: uses TRUSTED_CHECKS_* outside the environment")
        if not in_env:
            continue
        found.extend(_trusted_job_problems(name, job_name, job))
        if re.search(r"^\s*(ref|repository):", job, re.M):
            found.append(f"{name}:{job_name}: environment job checks out PR content")
        if _is_token_reference(job) and not APP_TOKEN_ACTION.search(job):
            found.append(f"{name}:{job_name}: create-github-app-token is not pinned to a SHA")
        if _is_token_reference(job) and re.search(
            r"(?:^|\n)\s*(?:['\"]?INPUT_[A-Za-z0-9_-]+['\"]?)\s*:", job
        ):
            found.append(f"{name}:{job_name}: token step uses INPUT_* environment keys")
        if _is_token_reference(job) and re.search(
            r"['\"](?:uses|permission-[a-z-]+)['\"]\s*:", job, re.I
        ):
            found.append(
                f"{name}:{job_name}: quoted permissions or uses key in token configuration"
            )
        if _is_token_reference(job) and re.search(
            r"&[A-Za-z0-9_-]+|\*[A-Za-z0-9_-]+|(?:^|\s)<<\s*:", job
        ):
            found.append(f"{name}:{job_name}: token configuration uses YAML anchors or aliases")
        # Old rule, kept: every permission-* anywhere in the job must be exactly the allow-list.
        job_wide = set(re.findall(r"^\s*(permission-[a-z-]+):\s*(\S+)", job, re.M | re.I))
        if job_wide and job_wide != ALLOWED_APP_PERMISSIONS:
            found.append(f"{name}:{job_name}: job-wide App token permissions {sorted(job_wide)}")
        # New rule, added: each token step on its own is non-empty and exactly the allow-list.
        for perms in app_token_permissions(job):
            if perms != ALLOWED_APP_PERMISSIONS:
                found.append(f"{name}:{job_name}: App token permissions {sorted(perms)}")
    return found
