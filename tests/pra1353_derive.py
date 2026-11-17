"""Pure row projection and comparison rules for the #1353 runner."""

from __future__ import annotations

import re


def derive(raw, recipe, platform):
    if isinstance(raw, dict):
        observed = str(raw.get("status", raw.get("error", raw.get("code", raw))))
        saved = {
            key: raw[key]
            for key in ("status", "error", "verified", "reason", "code", "detail", "outside")
            if key in raw
        }
    else:
        observed = str(raw)
        saved = raw
    status = raw.get("status", raw.get("error", "FAILED")) if isinstance(raw, dict) else raw
    reason_value = _rule_value(recipe["reason"], raw, recipe, status, platform)
    reason = re.sub(r"/(?:private/)?(?:var/folders|tmp)/[^\s:]+", "<temporary-path>", reason_value)
    if isinstance(saved, dict):
        saved = {key: _scrub(value) for key, value in saved.items()}
        if recipe["class_id"] == "C8" and "detail" in saved:
            saved["detail"] = re.sub(r"\b[0-9a-f]{40}\b", "<git-commit>", saved["detail"])
    expected = _rule_value(recipe["expected"], raw, recipe, status, platform)
    matches = observed == expected
    if expected == "REJECTED":
        matches = observed not in {"OK", "ACCEPTED"}
    elif expected == "NOT_RUN":
        matches = any(
            x in observed or x in reason for x in ("NO_SANDBOX", "BLACKBOX_NEEDS_VERIFIER_ACCOUNT")
        ) or (observed == "UNCOMPARABLE" and "sandbox" in reason.lower())
    elif expected == "VERIFICATION_INCOMPLETE":
        matches = "VERIFICATION_INCOMPLETE:" in observed
    elif expected == "PASS":
        matches = observed == "PASS" or ": PASS " in observed
    return {
        "seq": recipe["seq"],
        "class_id": recipe["class_id"],
        "probe": recipe["probe"],
        "route": recipe["route"],
        "observed": observed,
        "raw_return": saved,
        "expected_on_main": expected,
        "matches_spec": matches,
        "classification": _rule_value(recipe["classification"], raw, recipe, status, platform),
        "platform": platform,
        "reason": reason,
    }


def _rule_value(value, raw, recipe, status, platform):
    if not isinstance(value, str) or not value.startswith("@rule:"):
        return value
    rule = value.removeprefix("@rule:")
    if rule == "verify_reason":
        return raw.get("reason", "") if isinstance(raw, dict) else ""
    if rule == "blackbox_reason":
        return raw.get("reason", raw.get("detail", "")) if isinstance(raw, dict) else ""
    if rule.startswith("verification_classification:"):
        verified, refused = rule.split(":", 1)[1].split(",", 1)
        return verification_classification(status, verified, refused)
    if rule == "blackbox_classification":
        reason = raw.get("reason", raw.get("detail", "")) if isinstance(raw, dict) else ""
        return blackbox_classification(status, reason)
    if rule == "pass_classification":
        return "VERIFIED_WRONG" if "PASS" in str(raw) else "REFUSED"
    if rule == "c9_classification":
        return "NOT_RUN" if recipe["probe"] == "no-account" else "REFUSED"
    if rule == "c8_valid_classification":
        code = raw.get("code") if isinstance(raw, dict) else raw
        return "HONEST_VERIFIED" if code == "OK" else "HONEST_FAILED"
    if rule == "c10_reason":
        outside = raw.get("outside") if isinstance(raw, dict) else None
        return (
            f"outside-runner return={outside}; production runs "
            "python -P -m _cairnwork_blackbox_runner "
            "(src/cairnwork/blackbox_exec.py:139), so the name is absent from sys.modules"
        )
    if rule == "control_expected":
        if recipe["probe"] == "owner-correct":
            return "blackbox:add: PASS [matched 1/1] low-entropy"
        return "blackbox:add: MISMATCH [matched 0/1] low-entropy"
    raise ValueError(f"unknown derive rule: {rule}")


def verification_classification(status, verified, refused):
    if status == "TIMEOUT":
        return "HARNESS_TIMEOUT"
    return verified if str(status).startswith("VERIFIED") else refused


def _scrub(value):
    return (
        re.sub(r"/(?:private/)?(?:var/folders|tmp)/[^\s:]+", "<temporary-path>", value)
        if isinstance(value, str)
        else value
    )


def _gate_segments(reason):
    segments, start, depth = [], None, 0
    for index, char in enumerate(reason):
        if char == "[":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                segment = reason[start:index]
                if segment.startswith(("pytest-", "blackbox:", "protection:")):
                    segments.append(segment)
    return segments


def blackbox_classification(status, reason):
    if status == "TIMEOUT":
        return "HARNESS_TIMEOUT"
    failing = [
        item
        for item in _gate_segments(reason)
        if item.startswith("pytest-")
        or (item.startswith("blackbox:") and ": PASS " not in item and not item.endswith(": PASS"))
        or (item.startswith("protection:") and ": PASS " not in item)
    ]
    if failing and all(
        item.startswith("blackbox:")
        and "VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT" in item
        for item in failing
    ):
        return "NOT_RUN"
    return "REFUSED"
