"""Shared typed refusals used by task and candidate boundaries."""


class TaskRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class CandidateRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")
