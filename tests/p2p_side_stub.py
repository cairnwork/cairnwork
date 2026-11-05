"""#793: a stand-in for p2p_collect.collected_side in tests that stub the base probe: the test
commit collects every base id (nothing removed) and changes ``changed``."""

from dataclasses import replace

from cairnwork import p2p
from cairnwork.p2p_collect import CollectedSide


def stub_side(monkeypatch, changed=frozenset()):
    def fake(task, files, outcomes, test_files, ctx, budget, attempt_id):
        ids = frozenset(outcomes)
        edited = frozenset(test_files) & frozenset(files)
        return CollectedSide(ids, ids, frozenset(), frozenset(), edited, frozenset(changed))

    monkeypatch.setattr(p2p, "collected_side", fake)


def collected_run(run, probe):
    """``run`` as a clean collect-only run of the test commit that collects the probe's ids."""
    ids = tuple(probe.get("outcomes") or {})
    done = {"returncode": 0 if ids else 5, "collected": ids, "collection_finished": True}
    return replace(run, outcomes={}, recorded=True, **done)
