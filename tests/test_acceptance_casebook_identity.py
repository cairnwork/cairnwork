"""Acceptance casebook #772 AC1b round 2: identity normalisation, the family allow-list, the
human path and the independence rule's remaining branches (identity.py, no notary needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from experimental.acceptance_casebook.identity import (
    IdentityError,
    independence_problem,
    resolve_actor,
)

ENV = "CLAUDE_CODE_SESSION_ID"
URL = "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-1"


@pytest.fixture
def src(tmp_path: Path) -> str:
    path = tmp_path / "source.txt"
    path.write_text("dispatch output\n")
    return str(path)


def agent(src: str, sid: str, family: str = "google", lineage: str = "root") -> dict[str, Any]:
    opts = {"dispatch_id": sid, "model_family": family, "lineage": lineage, "source_file": src}
    return resolve_actor({}, opts)


def human(src: str, login: str) -> dict[str, Any]:
    return resolve_actor({}, {"github_login": login, "source_ref": URL, "source_file": src})


def claude(sid: str, lineage: str = "root") -> dict[str, Any]:
    return resolve_actor({ENV: sid}, {"lineage": lineage})


# Blocker 1: the human path


def test_login_refused_while_harness_session_is_set(src: str) -> None:
    with pytest.raises(IdentityError) as err:
        resolve_actor({ENV: "s1"}, {"github_login": "me", "source_ref": URL, "source_file": src})
    assert err.value.code == "IDENTITY_INVALID"


@pytest.mark.parametrize(
    "ref",
    [
        "u",
        "https://example.com/x#issuecomment-1",
        "https://github.com/a/b/issues/1",
        URL + "\n::warning::x",
    ],  # fmt: skip
)
def test_human_source_ref_must_be_a_comment_url(src: str, ref: str) -> None:
    with pytest.raises(IdentityError):
        resolve_actor({}, {"github_login": "me", "source_ref": ref, "source_file": src})


@pytest.mark.parametrize("body", ["", "  \n"])
def test_empty_source_file_refused(tmp_path: Path, body: str) -> None:
    empty = tmp_path / "e.txt"
    empty.write_text(body)
    with pytest.raises(IdentityError) as err:
        human(str(empty), "me")
    assert "empty" in err.value.detail and "\n" not in err.value.detail.replace("\\n", "")


def test_source_digest_rechecked_on_read(src: str) -> None:
    adder, conf = claude("s1"), human(src, "reviewer")
    assert independence_problem(adder, conf) is None
    Path(src).write_text("changed after the confirmation\n")
    assert independence_problem(adder, conf) == "IDENTITY_INCOMPLETE"


def test_error_text_does_not_echo_raw_path(tmp_path: Path) -> None:
    bad = str(tmp_path / "x\n::error::injected")
    with pytest.raises(IdentityError) as err:
        human(bad, "me")
    assert "\n::error::" not in str(err.value)
    assert not str(err.value).startswith("IDENTITY_INVALID: IDENTITY_INVALID")


# Blocker 2: normalisation and the family allow-list


@pytest.mark.parametrize(
    "variant", ["S1", " s1 ", "s1​", "​s1", "ｓ1", "S1\t"]
)  # case, whitespace, zero-width, full-width
def test_session_spelling_variants_are_the_same_session(variant: str) -> None:
    assert independence_problem(claude("s1"), claude(variant)) == "SAME_SESSION"


@pytest.mark.parametrize("variant", ["Me", " me", "ME‍", "ｍｅ"])
def test_login_spelling_variants_are_the_same_human(src: str, variant: str) -> None:
    assert independence_problem(human(src, "me"), human(src, variant)) == "SAME_SESSION"


@pytest.mark.parametrize("variant", ["sб1", "ѕ1"])  # Cyrillic look-alikes
def test_lookalike_ids_refused(variant: str) -> None:
    with pytest.raises(IdentityError):
        claude(variant)


@pytest.mark.parametrize(
    "lineage", ["S1", " s1", "x, s1", "x,,s1,", "x,S1​"]
)  # case, space after comma, empty items, invisible
def test_lineage_variants_still_name_the_adder(lineage: str) -> None:
    assert independence_problem(claude("s1"), claude("s2", lineage)) == "SAME_LINEAGE"


@pytest.mark.parametrize("lineage", ["", ",", " , "])
def test_empty_lineage_is_missing(lineage: str) -> None:
    assert "lineage" not in claude("s2", lineage)


@pytest.mark.parametrize("lineage", ["Root", "root ", "x,root"])
def test_only_exact_root_means_no_ancestors(lineage: str) -> None:
    with pytest.raises(IdentityError):
        claude("s2", lineage)


@pytest.mark.parametrize(
    "family", ["claude", "Anthropic", "anthropic ", "anthropic​", "аnthropic", "gemini"]
)
def test_family_aliases_refused(src: str, family: str) -> None:
    with pytest.raises(IdentityError):
        agent(src, "d1", family)


@pytest.mark.parametrize(
    "field", [("session_id", "S1"), ("session_id", "s1 "), ("model_family", "Google"),
              ("model_family", "claude"), ("lineage", ["S1"]), ("lineage", ["root"])]
)  # fmt: skip
def test_hand_written_unnormalised_identity_does_not_count(src: str, field: tuple) -> None:
    key, value = field
    conf = {**agent(src, "g1"), key: value}
    assert independence_problem(claude("s0"), conf) == "IDENTITY_INCOMPLETE"


# Surviving mutants from round 1


def test_adder_lineage_contains_confirmer(src: str) -> None:
    assert independence_problem(claude("s1", "g1"), agent(src, "g1")) == "SAME_LINEAGE"


def test_human_confirms_human_with_another_login(src: str) -> None:
    """Design intent: a human is independent of another human, whatever the family rule."""
    assert independence_problem(human(src, "alice"), human(src, "bob")) is None


def test_non_string_lineage_item_is_incomplete(src: str) -> None:
    conf = {**agent(src, "g1"), "lineage": [1]}
    assert independence_problem(claude("s1"), conf) == "IDENTITY_INCOMPLETE"


# Round 3 (review desk b on #826)


@pytest.mark.parametrize("env", [{ENV: ""}, {"CLAUDECODE": "1"}])
def test_login_refused_inside_claude_code_however_signalled(src: str, env: dict) -> None:
    with pytest.raises(IdentityError) as err:
        resolve_actor(env, {"github_login": "me", "source_ref": URL, "source_file": src})
    assert err.value.code == "IDENTITY_INVALID"


def test_unreadable_source_on_read_is_unconfirmed_not_an_error(src: str, tmp_path: Path) -> None:
    conf = {**agent(src, "g1"), "source_file": str(tmp_path)}  # a directory, not a file
    assert independence_problem(claude("s1"), conf) == "IDENTITY_INCOMPLETE"


@pytest.mark.parametrize(
    "ref",
    ["https://github.com/cairnwork/cairnwork/issues/772#issuecomment-",
     "https://github.com/someone/else/issues/772#issuecomment-1"],
)  # fmt: skip
def test_comment_url_needs_digits_and_this_repository(src: str, ref: str) -> None:
    with pytest.raises(IdentityError):
        resolve_actor({}, {"github_login": "me", "source_ref": ref, "source_file": src})


def test_human_link_rechecked_on_read(src: str) -> None:
    conf = {**human(src, "reviewer"), "source_ref": "not-a-link"}
    assert independence_problem(claude("s1"), conf) == "IDENTITY_INCOMPLETE"


@pytest.mark.parametrize(
    "change", [{"kind": "human"}, {"model_family": "google"}, {"source_sha256": "0" * 64}]
)
def test_env_source_needs_a_claude_agent_and_its_digest(change: dict) -> None:
    conf = {**claude("s2"), **change}
    assert independence_problem(claude("s1"), conf) == "IDENTITY_INCOMPLETE"
