"""Fail-closed check that deferred targets were collected and run."""

from cairnwork.p2p_function import _without_case


def not_run(runs, targets, speculative=frozenset()) -> list[str]:
    """Targets absent from outcomes, except proven imported-base AST ghosts (#1430)."""
    ran = {
        f
        for r in runs
        for n in r.outcomes
        for f in (_without_case(n), _without_case(n, all_parts=True))
    }
    missing = set(targets) - ran
    if runs:
        per_run = [set(getattr(r, "collected", ())) for r in runs]
        clean = all(
            getattr(r, "collection_finished", False)
            and getattr(r, "collected", ())
            and not getattr(r, "collect_errors", ())
            and not getattr(r, "collect_skipped", ())
            and set(getattr(r, "outcomes", ())) <= set(getattr(r, "collected", ()))
            for r in runs
        )
        if clean:
            collected = {_without_case(n) for group in per_run for n in group}
            absent = {
                target
                for target in missing
                if all(
                    _without_case(target) not in {_without_case(n) for n in group}
                    for group in per_run
                )  # fmt: skip
            }
            drop = {
                target
                for target in absent & set(speculative)
                if any(n.startswith(target.rsplit("::", 1)[0] + "::") for n in collected)
            }
            missing -= drop
    return sorted(missing)
