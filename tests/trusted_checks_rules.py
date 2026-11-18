"""Static rules for the trusted-checks boundary (used by tests/test_trusted_checks_app.py)."""

from __future__ import annotations

import re
from pathlib import Path

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


def strict_syntax_problems(name: str, text: str) -> list[str]:
    """Reject YAML forms outside the deliberately small trusted-workflow subset."""
    found: list[str] = []
    run_indent: int | None = None
    previous_indent: int | None = None
    previous_opens = False
    mapping = re.compile(r"^( *)(?:- )?([A-Za-z0-9_-]+):( .*)?$")
    sequence = re.compile(r"^( *)- ([^\s#&*!|>'\"%@`{}\[\],?:][^#]*)$")
    for number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if "input_" in lowered:
            found.append(f"{name}:{number}: strict YAML syntax forbids INPUT_")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if run_indent is not None and indent > run_indent:
            continue
        run_indent = None
        indentation_problem = _indentation_problem(
            name, number, indent, previous_indent, previous_opens
        )
        if indentation_problem:
            found.append(indentation_problem)
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
        # Only three inert flow forms: an empty permissions map, and flat lists of plain
        # lowercase words under branches/types (e.g. `types: [opened, synchronize]`).
        flow_ok = (key == "permissions" and value == "{}") or (
            key in {"branches", "types"}
            and re.fullmatch(r"\[ ?[a-z_]+(?:, ?[a-z_]+)* ?\]", value) is not None
        )
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
                found.append(
                    f"{name}:{number}: strict YAML syntax allows block scalars only for run"
                )
        if " #" in line and not re.fullmatch(
            r" *-? *uses: +[^#\s]+@[0-9a-fA-F]{40} +# v[^\s]+", line
        ):
            found.append(f"{name}:{number}: strict YAML syntax forbids inline comments")
        if _has_unclosed_quoted_scalar(line):
            found.append(f"{name}:{number}: strict YAML syntax forbids multi-line quoted scalars")
        previous_indent = indent
        previous_opens = bool(
            (match and not match.group(3))
            or (item and re.match(r"[A-Za-z0-9_-]+:", item.group(2)))
            or (match and line[indent:].startswith("- "))
        )
    return found


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
    for index, line in enumerate(lines):
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
    for index, line in enumerate(lines):
        if re.search(r"\b(?:secrets|vars)\s*\[", line, re.I):
            found.append(f"{name}:{index + 1}: indexed secrets or vars references are forbidden")
        if _whole_secrets_reference(line):
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
            child = lines[child_index]
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


def problems(name: str, text: str) -> list[str]:
    """Every rule violation in one workflow file, as short messages."""
    found = strict_syntax_problems(name, text) if _is_subject(text) else []
    found.extend(_environment_problems(name, text))
    lowered = text.lower()
    mentions = ENV_NAME in lowered or "trusted_checks_" in lowered or _is_token_reference(text)
    if not mentions:
        return found
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
