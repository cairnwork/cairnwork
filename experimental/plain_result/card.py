"""An evidence card: one result record split into four fixed cells (tested, confirmed problem,
not covered, undetermined). Every line names the record field it comes from, so a reader can
check it against the record. Read-only; it never decides or changes a verdict, and it reads only
what ``summarize`` already trusts."""

from __future__ import annotations

import re

from experimental.plain_result.summary import summarize

# The reason text of a FAILED run is wording only (reasons.py). Only the one shape that says
# nothing but "the tests failed" counts as a problem: every failed verifier's only cause is
# ``pytest exit code 1`` (reasons.explain). Anything else (a timeout, a size cap, leftover
# processes, an environment or setup error, a note, a check or gate) is undetermined.
TEST_FAILURE = re.compile(
    r"[^\[\]]* \[[^:;\[\]]+: pytest exit code 1(?:; [^:;\[\]]+: pytest exit code 1)*\]"
)
NOT_COVERED = "整體涵蓋範圍：未知。沒有需求對應到測試的紀錄（#699），通過不代表全部需求都測過。"


def _line(text: str, source: str) -> dict:
    return {"text": text, "source": source}


def _plain_test_failure(record: dict, current_commit: str | None) -> bool:
    """FAILED for this commit, with a reason that says only that tests failed."""
    reason = record["reason"]
    same = current_commit is None or record["candidate_commit"] == current_commit
    return same and isinstance(reason, str) and TEST_FAILURE.fullmatch(reason) is not None


def _tested(record: dict) -> list[dict]:
    lines = [
        _line(f"檢查的版本：{record['candidate_commit'][:8]}", "candidate_commit"),
        _line("Core 判決：VERIFIED", "core_status"),
        _line(f"保護等級：{record['protection']}", "protection"),
    ]
    binding = record["binding"]
    if isinstance(binding, dict):
        ids = [v[0] for v in binding.get("verifiers") or () if isinstance(v, list) and v]
        if ids:
            lines.append(_line(f"跑過的檢查：{', '.join(map(str, ids))}", "binding.verifiers"))
        paths = binding.get("test_paths")
        if isinstance(paths, list) and paths:
            lines.append(_line(f"延後測試檔：{', '.join(map(str, paths))}", "binding.test_paths"))
    return lines


def _undetermined(record: dict, state: str, headline: str) -> list[dict]:
    if state == "uncomparable" and isinstance(record.get("uncomparable"), dict):
        code = record["uncomparable"].get("code", "")
        return [_line(f"無法比較修改前後：{code}", "uncomparable.code")]
    if state == "refused":
        return [_line(f"沒有執行驗收，規則拒收：{record['reason']}", "reason")]
    if state == "failed":
        return [_line(f"有檢查沒有通過，原因無法判定：{record['reason']}", "reason")]
    if state == "stale_commit":
        return [_line(headline, "candidate_commit")]
    if state in ("weak_assurance", "attested"):
        runs = record["assurance"].get("verifier_runs")
        return [_line(f"證明可以核驗，但測試是否真的執行：{runs}", "assurance.verifier_runs")]
    if "error" in record and "status" not in record:  # verify_cli's pre-Core refusal or error
        return [_line(f"沒有執行驗收：{record['error']}", "error")]
    return [_line(headline, "")]


def card(
    record: dict,
    current_commit: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> dict:
    """The four cells for ``record``; the arguments are those of ``summarize``. Only a record
    ``summarize`` shows as verified fills "tested"; "not covered" is never empty."""
    shown = summarize(record, current_commit, task_id, attempt_id)
    state = shown["state"]
    record = shown["source"]
    cells: dict = {"tested": [], "confirmed_problem": [], "not_covered": [], "undetermined": []}
    cells["not_covered"].append(_line(NOT_COVERED, ""))
    if state == "verified":
        cells["tested"] = _tested(record)
    elif state == "failed" and _plain_test_failure(record, current_commit):
        text = f"有檢查沒有通過；原因尚未排除環境因素：{record['reason']}"
        cells["confirmed_problem"].append(_line(text, "reason"))
    else:
        cells["undetermined"] = _undetermined(record, state, shown["headline"])
    return {"state": state, "headline": shown["headline"], **cells, "source": record}
