"""PAUSE CommandTransport adapter for ``claude-api-run``.

``CommandTransport`` speaks JSON over stdin/stdout (``{model, effort, messages}`` in,
``{status, body}`` out) but ``claude-api-run --tag T -- claude -p ...`` is a budget-gating wrapper
around the Claude CLI. This shim converts between them so the Haiku gate really bills (and is
capped by) the registered tag. Exit 3 from the wrapper is its budget gate: ``quota_blocked``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_RUNNER = str(Path.home() / ".local/bin/claude-api-run")
BUDGET_EXIT = 3


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def build_prompt(messages: list[dict]) -> str:
    return "\n\n".join(_text(m.get("content", "")) for m in messages if isinstance(m, dict))


def parse_result(stdout: str) -> dict | None:
    """The CLI's final ``type == result`` JSON object (single object or JSON lines)."""
    found = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    if found is None:
        try:
            whole = json.loads(stdout)
        except ValueError:
            return None
        found = whole if isinstance(whole, dict) and whole.get("type") == "result" else None
    return found


def envelope(request: dict, tag: str, runner: str, timeout: float) -> dict:
    argv = [
        runner,
        "--tag",
        tag,
        "--",
        "claude",
        "-p",
        "--model",
        str(request.get("model", "haiku")),
        "--output-format",
        "json",
    ]
    prompt = build_prompt(request.get("messages", []))
    try:
        run = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return {"status": 0, "timed_out": True}
    except OSError:
        return {"status": -1}
    if run.returncode == BUDGET_EXIT:
        return {"status": 0, "quota_blocked": True}
    result = parse_result(run.stdout)
    if run.returncode != 0 or result is None or result.get("is_error"):
        return {"status": -1}
    raw_usage = result.get("usage")
    usage: dict = raw_usage if isinstance(raw_usage, dict) else {}
    return {
        "status": 200,
        "body": {
            "text": str(result.get("result", "")),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
            "cost_usd": result.get("total_cost_usd"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--runner", default=DEFAULT_RUNNER)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    try:
        request = json.loads(sys.stdin.read())
    except ValueError:
        print(json.dumps({"status": -1}))
        return 0
    print(json.dumps(envelope(request, args.tag, args.runner, args.timeout)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
