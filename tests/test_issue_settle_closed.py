"""settle lists running Issues of any state, so one closed mid-run is still settled (#632)."""

import json

import pytest

import cairnwork.issue_runner as runner
from cairnwork.issue_github import GitHub


class Listing(GitHub):
    def __init__(self):
        super().__init__("/bin/false", "James3014/demo")
        self.argv = []

    def _run(self, *args, stdin=None):
        self.argv.append(args)
        return json.dumps([])


@pytest.mark.parametrize(
    ("label", "state"),
    [(runner.RUNNING, "all"), (runner.QUEUE, "open")],
)
def test_only_the_running_listing_includes_closed_issues(label, state):
    github = Listing()
    github.issues(label)
    argv = github.argv[0]
    assert argv[argv.index("--state") + 1] == state  # a closed queued Issue is never taken
