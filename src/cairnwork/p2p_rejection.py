"""P2P-specific refusal type."""

from cairnwork.deferred_tests import TestCommitRejected


class P2PRejected(TestCommitRejected):
    """The P2P step could not run safely: the task is REFUSED, never verified."""
