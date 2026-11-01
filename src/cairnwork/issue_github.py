"""The GitHub CLI client the Issue runner drives.

Split out of issue_runner.py to keep that file under the 400-line cap (#164 and #168 were each
under 400 but summed to 410 once merged). Behaviour is unchanged: every call shells out to the
pinned ``gh`` wrapper with a fixed argv and no shell.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any
from urllib.parse import urlencode

from cairnwork.issue_task import TRUST_CHECK_FAILED, queue_trusted

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class GitHub:
    def __init__(self, gh: str, repo: str) -> None:
        if not REPO.fullmatch(repo):
            raise ValueError(f"repo must be OWNER/NAME, got {repo!r}")
        self.gh, self.repo = gh, repo

    def _run(self, *args: str, stdin: str | None = None) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - fixed binary, argv list, no shell
                [self.gh, *args], input=stdin, capture_output=True, text=True, timeout=120,
                check=False,
            )  # fmt: skip
        except subprocess.SubprocessError as exc:  # a hung gh fails this Issue, not the pass (#648)
            raise RuntimeError(f"gh {args[0]} failed: {type(exc).__name__}") from exc
        if done.returncode != 0:
            raise RuntimeError(f"gh {args[0]} failed: {done.stderr.strip()[-300:]}")
        return done.stdout

    def issues(self, label: str) -> list[dict[str, Any]]:
        out = self._run(
            "issue", "list", "-R", self.repo, "--state", "open", "--label", label,
            "--limit", "50", "--json", "number,title,body",
        )  # fmt: skip
        return json.loads(out)

    def trust(self, number: int, label: str) -> str:
        try:
            return self._trust(number, label)
        except (TypeError, AttributeError, KeyError, ValueError):  # odd GitHub output: refuse
            return TRUST_CHECK_FAILED

    def current(self, number: int) -> dict[str, Any]:
        out = self._run("issue", "view", str(number), "-R", self.repo, "--json", "body,title")
        return json.loads(out)

    def _trust(self, number: int, label: str) -> str:
        jq = (
            '.[] | select(.event=="labeled" or .event=="renamed") | '
            "{event, label: .label.name, actor: .actor.login, at: .created_at}"
        )
        out = self._run(
            "api", "--paginate", f"repos/{self.repo}/issues/{number}/timeline", "--jq", jq
        )
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        owner, name = self.repo.split("/")
        query = (
            f'query($num:Int!){{repository(owner:"{owner}",name:"{name}"){{issue(number:$num)'
            "{author{login __typename} userContentEdits(first:100)"
            "{totalCount nodes{editedAt editor{login __typename}}}}}}"
        )
        data = json.loads(
            self._run("api", "graphql", "-f", f"query={query}", "-F", f"num={number}")
        )
        issue = (((data or {}).get("data") or {}).get("repository") or {}).get("issue") or {}
        return queue_trusted(
            label=label, events=events, author=issue.get("author"),
            edits=issue.get("userContentEdits"),
        )  # fmt: skip

    def comment(self, number: int, text: str) -> None:
        self._run("issue", "comment", str(number), "-R", self.repo, "--body-file", "-", stdin=text)

    def relabel(self, number: int, add: str, remove: str) -> None:
        # two calls, add first: a pass that stops halfway leaves both labels, which the runner
        # settles, never neither, which no pass lists again (#634)
        self._run("issue", "edit", str(number), "-R", self.repo, "--add-label", add)
        self._run("issue", "edit", str(number), "-R", self.repo, "--remove-label", remove)

    def parents(self, commit: str) -> list[str]:
        out = self._run(
            "api", f"repos/{self.repo}/commits/{commit}", "--jq", ".parents[].sha"
        )  # fmt: skip
        return out.split()

    def find_pr(self, branch: str, commit: str) -> str:
        """This repo's PR from ``branch`` into main at ``commit``, or "" (#403). Asked with
        ``head=<owner>:<branch>``, since a bare branch name matches every fork's branch of that
        name; whatever comes back is checked again. A PR of ours at another commit, or one
        closed unmerged, is refused: someone changed it, and a person has to look."""
        owner = self.repo.split("/")[0]
        query = urlencode({"head": f"{owner}:{branch}", "state": "all", "per_page": "100"})
        prs = json.loads(self._run("api", f"repos/{self.repo}/pulls?{query}"))
        if not isinstance(prs, list) or not all(isinstance(pr, dict) for pr in prs):
            raise ValueError("gh api pulls returned an unexpected answer")
        ours = [
            pr for pr in prs
            if _get(pr, "head", "repo", "full_name").casefold() == self.repo.casefold()
            and _get(pr, "head", "ref") == branch and _get(pr, "base", "ref") == "main"
        ]  # fmt: skip
        at_commit = [pr for pr in ours if _get(pr, "head", "sha") == commit]
        live = [pr for pr in at_commit if pr.get("state") == "open" or pr.get("merged_at")]
        if live:
            if not _get(live[0], "html_url"):
                raise ValueError("gh api pulls returned a PR without a URL")
            return _get(live[0], "html_url")
        if at_commit:
            raise ValueError(f"PR_CLOSED: the PR from {branch} was closed unmerged")
        if ours:
            raise ValueError(f"PR_HEAD_MISMATCH: the PR from {branch} is not at {commit}")
        return ""

    def draft_pr(self, branch: str, title: str, body: str) -> str:
        return self._run(
            "pr", "create", "-R", self.repo, "--draft", "--base", "main", "--head", branch,
            "--title", title, "--body-file", "-", stdin=body,
        ).strip()  # fmt: skip


def _get(data: dict[str, Any], *keys: str) -> str:
    """The string at ``keys``, or "" when any step is missing or not what GitHub documents."""
    value: Any = data
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, str) else ""
