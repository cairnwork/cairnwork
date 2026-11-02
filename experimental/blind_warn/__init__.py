"""Blind-test warning line (experimental, default off): two independent blind tests, one warning
line, never a verdict. See judge.py and README.md."""

from experimental.blind_warn.judge import run_blind_warn

__all__ = ["run_blind_warn"]
