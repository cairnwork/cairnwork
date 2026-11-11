"""Deterministic, read-only summaries of known Cairnwork result records."""

from __future__ import annotations

import re

_OID = re.compile(r"^[0-9a-f]{40}$")
_NO_TRUST = "目前還沒有可信的結果"


def _result(headline: str, trust: str, next_action: str, state: str, record: dict) -> dict:
    return {
        "headline": headline,
        "trust": trust,
        "next_action": next_action,
        "state": state,
        "source": record,
    }


def _unknown(record: dict) -> dict:
    return _result(
        _NO_TRUST,
        "目前無法確認這份資料代表什麼。",
        "先確認結果資料完整且來自受支援的檢查。",
        "no_trustworthy_result",
        record,
    )


def _tool_error(record: dict) -> dict:
    return _result(
        "檢查工具沒有順利完成。",
        "目前沒有可用的檢查結果，不能判定程式通過或寫錯。",
        "確認工具可用後重新檢查。",
        "tool_error",
        record,
    )


def _not_checked(record: dict) -> dict:
    return _result(
        "Cairnwork 沒有檢查這個改動。",
        f"這是 Cairnwork 檢查範圍的限制（{record['error']}），"
        "不是對改動的判斷；既不是通過，也不是失敗。",
        "請人審查，或改用其他方式檢查。",
        "not_checked",
        record,
    )


def is_plain_refusal(record: dict) -> bool:
    """Whether a pre-Core REFUSED record is the plain error shape."""
    error = record.get("error")
    return (
        record.get("status") == "REFUSED"
        and record.get("reason") == error
        and record.get("verified") is False
        and isinstance(error, str)
        and bool(error)
        and not any(key in record for key in ("ok", "code"))
    )


def _verify(record: dict, current_commit: str | None) -> dict:
    needed = (
        "status",
        "verified",
        "core_status",
        "candidate_commit",
        "task_id",
        "attempt_id",
        "binding",
        "protection",
        "reason",
    )
    if any(key not in record for key in needed):
        return _unknown(record)
    if not all(isinstance(record[key], str) and record[key] for key in ("task_id", "attempt_id")):
        return _unknown(record)
    candidate = record["candidate_commit"]
    if not isinstance(candidate, str) or not _OID.fullmatch(candidate):
        return _unknown(record)
    binding = record["binding"]
    if binding is not None:
        if not isinstance(binding, dict) or binding.get("head") != candidate:
            return _unknown(record)
        for key in ("task_id", "attempt_id"):
            if key in binding and binding[key] != record[key]:
                return _unknown(record)
    return _verify_outcome(record, candidate, current_commit)


def _verify_outcome(record: dict, candidate: str, current_commit: str | None) -> dict:
    if (
        record["status"] == "VERIFIED"
        and record["verified"] is True
        and record["core_status"] == "VERIFIED"
    ):
        if current_commit is not None and current_commit != candidate:
            return _result(
                f"先前檢查的是 {candidate[:8]} 版，現在是 "
                f"{current_commit[:8]} 版，還沒有重新檢查。",
                "先前的檢查只適用於當時的版本。",
                "先驗目前這一版。",
                "stale_commit",
                record,
            )
        if record["protection"] not in (
            "blackbox",
            "partial",
            "blackbox_macos",
            "partial_macos",
            "same_process_only",
        ):
            return _unknown(record)
        if current_commit is None:
            return _result(
                f"{candidate[:8]} 版通過了安排的檢查，但沒有提供目前版本，不能確認它就是這個改動。",
                "檢查結果只適用於已知的候選版本；目前版本未知，因此不能視為目前工作的通過結果。",
                "提供目前版本後再讀取結果。",
                "verified_version_unknown",
                record,
            )
        return _result(
            "這一版通過了安排的檢查。仍有檢查不到的地方。",
            f"檢查適用於 {candidate[:8]} 版；保護等級是 {record['protection']}；"
            "檢查只涵蓋安排的項目，仍可能有沒發現的問題。",
            "請人確認能否交付。",
            "verified",
            record,
        )
    status = record["status"]
    if status == "FAILED":
        return _result(
            "這版有檢查沒有通過。",
            "目前不能當成已完成；失敗原因需要查看紀錄。",
            "查看失敗紀錄並找出原因。",
            "failed",
            record,
        )
    if status == "UNCOMPARABLE":
        return _result(
            "目前無法有效比較修改前後。",
            "這不代表程式寫錯，也不代表檢查成功。",
            "恢復比較條件後重新檢查。",
            "uncomparable",
            record,
        )
    if status == "REFUSED":
        return _result(
            "送交的內容不符合這次驗收規則。",
            f"觸發規則限制：{record['reason']}。不能當成完成，也不表示程式必定寫錯。",
            "核對工單和允許修改範圍。",
            "refused",
            record,
        )
    return _unknown(record)


def _attest(record: dict, current_commit: str | None) -> dict:
    if not all(key in record for key in ("ok", "code", "candidate", "core_status", "test_source")):
        return _unknown(record)
    if record["ok"] is False and record["code"] == "USAGE":
        return _tool_error(record)
    if not isinstance(record["candidate"], str) or not _OID.fullmatch(record["candidate"]):
        return _unknown(record)
    if record["ok"] is False:
        unverified = record.get("unverified_fields")
        if not isinstance(unverified, list):
            return _unknown(record)
        return _result(
            "有人交出一份聲稱通過的證明，但目前無法證明它有效。",
            "核驗沒有成功；其中標為未核驗的欄位不能當作證據。",
            "重新取得並核驗有效證明。",
            "attestation_unverified",
            record,
        )
    if record["ok"] is not True or record["code"] != "OK" or record["core_status"] != "VERIFIED":
        return _unknown(record)
    if current_commit is not None and record["candidate"] != current_commit:
        return _result(
            f"先前檢查的是 {record['candidate'][:8]} 版，現在是 "
            f"{current_commit[:8]} 版，還沒有重新檢查。",
            "先前的檢查只適用於當時的版本。",
            "先驗目前這一版。",
            "stale_commit",
            record,
        )
    assurance = record.get("assurance")
    if not isinstance(assurance, dict):
        return _unknown(record)
    weak = (
        record["test_source"] == "auto"
        or assurance.get("mode") in (None, "unknown", "signer_claim")
        or assurance.get("protection") in (None, "same_process_only")
        or assurance.get("verifier_runs") in (None, "unknown", "signer_claim")
        or assurance.get("test_contents") in (None, "unknown", "not_verified")
    )
    if weak:
        return _result(
            "這份證明可以核驗，但仍有安全條件沒有獲得證明。",
            "核驗成立的只有這份證明本身；測試來源與保護條件沒有獲得證明，不能當成完整的安全保證。",
            "依未證明部分補查，並請人審查。",
            "weak_assurance",
            record,
        )
    return _result(
        "這份證明已核驗。仍需確認檢查範圍是否符合交付要求。",
        "證明只支持其中記載的檢查與條件。",
        "請人確認能否交付。",
        "attested",
        record,
    )


def summarize(
    record: dict,
    current_commit: str | None = None,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> dict:
    """Summarize a supported result record without I/O or changing its verdict. ``task_id`` and
    ``attempt_id`` name the work the caller reports on; a record for other work is not trusted."""
    if not isinstance(record, dict):
        return _unknown({})
    for key, wanted in (("task_id", task_id), ("attempt_id", attempt_id)):
        if wanted is not None and record.get(key) != wanted:
            return _unknown(record)
    error = record.get("error")
    if is_plain_refusal(record):
        if error in ("TASK_ALLOWED_FILES_PROTECTED", "VERIFIER_NOT_ALLOWED"):
            return _not_checked(record)
        return _tool_error(record)
    if (
        isinstance(error, str)
        and error
        and not any(key in record for key in ("status", "ok", "verified", "code"))
    ):
        if error in ("TASK_ALLOWED_FILES_PROTECTED", "VERIFIER_NOT_ALLOWED"):
            return _not_checked(record)
        return _tool_error(record)
    if "code" in record and "ok" not in record and "status" not in record:
        return _tool_error(record)
    if current_commit is not None and (
        not isinstance(current_commit, str) or not _OID.fullmatch(current_commit)
    ):
        return _unknown(record)
    if "status" in record or "verified" in record:
        return _verify(record, current_commit)
    if "ok" in record:
        return _attest(record, current_commit)
    return _unknown(record)
