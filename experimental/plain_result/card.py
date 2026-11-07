"""An evidence card: one result record split into four fixed cells (tested, confirmed problem,
not covered, undetermined). Every line names the record field it comes from, so a reader can
check it against the record. Read-only; it never decides or changes a verdict, and it reads only
what ``summarize`` already trusts."""

from __future__ import annotations

from experimental.plain_result.summary import summarize

# The reason text of a FAILED run is wording only (reasons.py). Any of these phrases means the run
# environment may have caused the failure, so the failure is not shown as a confirmed problem.
ENV_WORDS = (
    "no sandbox backend",
    "setup error",
    "environment error",
    "missing pytest plugin",
    "likely the run environment",
)
NOT_COVERED = "整體涵蓋範圍：未知。沒有需求對應到測試的紀錄（#699），通過不代表全部需求都測過。"


def _line(text: str, source: str) -> dict:
    return {"text": text, "source": source}


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
        if paths:
            lines.append(_line(f"延後測試檔：{', '.join(map(str, paths))}", "binding.test_paths"))
    return lines


def _undetermined(record: dict, state: str, headline: str) -> list[dict]:
    if state == "uncomparable" and isinstance(record.get("uncomparable"), dict):
        code = record["uncomparable"].get("code", "")
        return [_line(f"無法比較修改前後：{code}", "uncomparable.code")]
    if state == "refused":
        return [_line(f"沒有執行驗收，規則拒收：{record['reason']}", "reason")]
    if state == "failed":
        return [_line(f"有檢查沒有通過，可能是執行環境造成：{record['reason']}", "reason")]
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
    elif state == "failed" and not any(word in record["reason"] for word in ENV_WORDS):
        text = f"有檢查沒有通過；原因尚未排除環境因素：{record['reason']}"
        cells["confirmed_problem"].append(_line(text, "reason"))
    else:
        cells["undetermined"] = _undetermined(record, state, shown["headline"])
    return {"state": state, "headline": shown["headline"], **cells, "source": record}
