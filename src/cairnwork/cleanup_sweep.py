"""What a dead run's cleanup must look at before it says "cleaned" (#1827).

The sweep for a dead run names the verifier account (its processes are another uid's, so the
owner can only count them) and the run's own directories (an owner process whose environment is
cleared is tied to the run by its working directory). Both the dead-run cleanup of
verify_candidate and recovery build their sweep from these helpers, so the two cannot drift.
A look that is not clean keeps the attempt "kept": the leftover scan lists and then inspects,
so while a live process is moving entries an empty list is not evidence (a snapshot race, not an
error to swallow).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from functools import partial

from cairnwork import confined
from cairnwork.confined import ATTEMPT_ENV, Sweep
from cairnwork.proc_cwd import attempt_dirs


def user_of(account) -> str | None:
    """The verifier account's user name when it can own processes the owner must account for;
    None for no account, a stand-in without a user, a user that does not exist, or the owner's
    own uid (never swept as "another user's")."""
    user = getattr(account, "user", None)
    if not isinstance(user, str) or not user:
        return None
    uid = confined._account_uid(user)
    return None if uid is None or uid == os.getuid() else user


def run_dirs(attempt_id: str, run_root, account=None) -> tuple[str, ...]:
    """The attempt's run directories (realpaths) under the owner's run root and the verifier
    account's, taken before cleanup removes them (proc_cwd)."""
    extra = [account.run_root] if account is not None and hasattr(account, "run_root") else []
    return tuple(str(d) for d in attempt_dirs([run_root, *extra], attempt_id))


def merged(first: Sweep, second: Sweep) -> Sweep:
    """Two looks (one per account) as one: not clean if either is not."""
    return replace(
        first,
        listed=first.listed and second.listed,
        killed=first.killed + second.killed,
        denied=first.denied + second.denied,
        unreadable=first.unreadable + second.unreadable,
        account=first.account + second.account,
        held_by=first.held_by + second.held_by,
        restricted=first.restricted or second.restricted,
        vanished=first.vanished + second.vanished,
        unconfirmed=first.unconfirmed or second.unconfirmed,
        argv=first.argv + second.argv,
    )


def look_at(sweep: Callable[..., Sweep], head: tuple[str, str], accounts, **kw) -> Sweep:
    """``sweep(*head, account=a, **kw)`` once per distinct account named (once with None when
    none is), merged."""
    names = list(dict.fromkeys(a for a in accounts if a)) or [None]
    out = sweep(*head, account=names[0], **kw)
    for name in names[1:]:
        out = merged(out, sweep(*head, account=name, **kw))
    return out


def look_for(sweep: Callable[..., Sweep], intake, attempt_id: str, mark: str):
    """The look of a dead run's cleanup: ``sweep`` (sweep_marked) naming the verifier account and
    the run's directories, taken before cleanup removes them. An account that cannot be loaded
    names nothing here; ``clear_account`` then keeps the attempt (its root is unreadable)."""
    try:
        account = intake.verifier_account()
    except Exception:  # noqa: BLE001 - refused account: the leftover step reports it
        account = None
    dirs = run_dirs(attempt_id, intake.run_root, account)
    return partial(sweep, ATTEMPT_ENV, attempt_id, dispatched=mark, cwd_dirs=dirs,
                   account=user_of(account))  # fmt: skip
