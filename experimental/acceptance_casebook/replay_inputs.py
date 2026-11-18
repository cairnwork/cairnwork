"""Fail-closed validation for the versioned replay input whitelist."""

from __future__ import annotations

import re
import shlex
from typing import Any

from experimental.acceptance_casebook.store import CasebookError

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PYTEST_ID = re.compile(r"pytest-(?:0|[1-9][0-9]*)\Z")


def bound_inputs(
    case: dict[str, Any], inputs: Any, request: dict[str, Any], binding: Any = None
) -> dict[str, Any]:
    raw = inputs.get(case["line_sha256"]) if isinstance(inputs, dict) else None
    allowed = {
        "allowed_file",
        "verifier",
        "test_commit",
        "import_path",
        "blackbox",
        "require_protection",
    }
    if not isinstance(raw, dict) or not {"allowed_file", "verifier"} <= set(raw):
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    if set(raw) - allowed:
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    for key in ("base", "candidate"):
        value = case.get(key)
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise CasebookError("REPLAY_INPUT_MISMATCH")
    contract, plan = request.get("acceptance_contract", {}), request.get("verification_plan", {})
    allowed_paths = contract.get("allowed_paths", request.get("allowed_paths"))
    supplied_paths = raw["allowed_file"]
    if (
        not isinstance(supplied_paths, list)
        or not all(isinstance(value, str) for value in supplied_paths)
        or not isinstance(allowed_paths, list)
        or sorted(supplied_paths) != sorted(allowed_paths)
    ):
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    commands = raw["verifier"]
    required_ids = plan.get(
        "required_verifier_ids",
        contract.get("required_verifier_ids", request.get("required_verifier_ids")),
    )
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) and command.strip() for command in commands)
        or len(set(commands)) != len(commands)
        or not isinstance(required_ids, list)
        or not all(isinstance(value, str) and _PYTEST_ID.fullmatch(value) for value in required_ids)
        or len(set(required_ids)) != len(required_ids)
    ):
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    derived_ids = {f"pytest-{index}" for index in range(len(commands))}
    if set(required_ids) != derived_ids:
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    _check_recorded_commands(commands, binding)
    optional = {
        "test_commit": ("test_commit", ""),
        "import_path": ("import_paths", []),
        "blackbox": ("blackbox", None),
        "require_protection": ("require_protection", False),
    }
    for key, (request_key, default) in optional.items():
        value = raw.get(key, default)
        if not _valid(key, value):
            raise CasebookError("REPLAY_INPUT_MISMATCH")
        if request_key in request:
            if value != request[request_key]:
                raise CasebookError("REPLAY_INPUT_MISMATCH")
        elif value != default:
            raise CasebookError("REPLAY_INPUT_MISMATCH")
    return {**raw, "base": case["base"], "candidate": case["candidate"]}


def _check_recorded_commands(commands: list[str], binding: Any) -> None:
    if binding is None:
        return
    recorded = binding.get("verifiers") if isinstance(binding, dict) else None
    if recorded is None:
        return
    try:
        expected = {item[0]: item[1] for item in recorded}
        supplied = {
            f"pytest-{index}": shlex.split(command) for index, command in enumerate(commands)
        }
    except (IndexError, TypeError, ValueError):
        raise CasebookError("REPLAY_INPUT_MISMATCH") from None
    if (
        len(expected) != len(recorded)
        or any(
            not isinstance(key, str) or not isinstance(argv, list) for key, argv in expected.items()
        )
        or expected != supplied
    ):
        raise CasebookError("REPLAY_INPUT_MISMATCH")


def _valid(key: str, value: Any) -> bool:
    if key == "test_commit":
        return isinstance(value, str) and (not value or bool(_SHA.fullmatch(value)))
    if key == "import_path":
        return isinstance(value, list) and all(isinstance(part, str) for part in value)
    if key == "blackbox":
        return value is None or isinstance(value, str)
    return type(value) is bool
