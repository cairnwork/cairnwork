"""Workers that edit an attempt clone (see cairnwork.pipeline.Worker)."""


class WorkerDefaults:
    """Base for a worker with no run account and no isolation (a test double, a wrapper)."""

    run_as: str = ""
    isolated: bool = False
    not_isolated_because: str = ""
