"""Workers that edit an attempt clone (see cairnwork.pipeline.Worker)."""


class WorkerDefaults:
    """Base for a worker with no run account and no isolation (a test double, a wrapper)."""

    @property
    def run_as(self) -> str:
        return ""

    @property
    def isolated(self) -> bool:
        return False

    @property
    def not_isolated_because(self) -> str:
        return ""
