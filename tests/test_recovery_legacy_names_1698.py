"""#1698 review: run directories made before the opaque names (``<attempt>-v-v0.<rand>``, the Core
run's id in the name) are still told apart by recovery: the verify log stays, only ``run/`` goes."""

from test_recovery_leftovers import (
    _crashed_attempt,
    _leftover_names,
    _recover,
    crash_project,  # noqa: F401 - fixture, requested by name below
    project,  # noqa: F401 - fixture, the one crash_project asks for
)


def test_a_legacy_core_run_directory_keeps_its_log_and_loses_its_scratch(
    crash_project,  # noqa: F811 - the imported fixture
    tmp_path,
):
    _, attempt = _crashed_attempt(crash_project, tmp_path)
    legacy = tmp_path / "runs" / f"{attempt}-v-v0.abcd1234"  # no .evidence mark: the old name rule
    (legacy / "run" / "home").mkdir(parents=True)
    (legacy / "verifier.log").write_text("the verify log")
    other = tmp_path / "runs" / f"{attempt}-b-v0.abcd1234"  # any other old tag goes whole
    (other / "run").mkdir(parents=True)
    (other / "verifier.log").write_text("a base run's log")
    (decision,) = _recover(tmp_path)
    assert decision.detail == ""
    assert (legacy / "verifier.log").read_text() == "the verify log"
    assert not (legacy / "run").exists() and not other.exists()
    assert _leftover_names(tmp_path / "runs", attempt) == []
