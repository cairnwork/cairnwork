"""Base teeth for oracle targets (#1039 AC5): does the probe fail on the base commit?

``classify_base`` reads one ``run_probe`` result on the base (no candidate code runs there, so the
exception chain the probe runner writes to its own chain file is as trusted as base tracebacks are
for ``base_teeth``; only a chain file that ends with the runner's nonce is read, stderr never)
and returns:
- TEETH: at least one strong key's emitted value differs from ``expected`` or is missing, or the
  base crashed with an ordinary exception (an ImportError or AttributeError for a function that
  does not exist yet; ``fail_kind`` None in ``base_teeth``);
- NO_TEETH: only weak keys differ, or every key is equal. The target is refused;
- UNCOMPARABLE ``BASE_ENV_ERROR``: the chain holds an OSError, MemoryError, RecursionError or a
  child that could not run (env_failure.py classes; a child that merely exits non-zero does not
  count), so the base failed from the environment as much as from the code;
- UNCOMPARABLE ``BASE_NO_OUTPUT``: no output and no traceback (killed, timed out, or an output that
  cannot be read).
It only ever moves a target toward refusal; it changes no verdict of a candidate run."""

from __future__ import annotations

from dataclasses import dataclass

from experimental.oracle_observer.comparator import Invalid, parse_document, same
from experimental.oracle_observer.expected import Expected, strong_keys
from experimental.oracle_observer.run import ProbeRun

TEETH, NO_TEETH, UNCOMPARABLE = "TEETH", "NO_TEETH", "UNCOMPARABLE"
BASE_ENV_ERROR, BASE_NO_OUTPUT = "BASE_ENV_ERROR", "BASE_NO_OUTPUT"
PREFIX = "ORACLE_CHAIN "
END = "ORACLE_CHAIN_END"


@dataclass(frozen=True)
class BaseResult:
    kind: str
    code: str = ""

    @property
    def refused(self) -> bool:
        """True when the target must not be used (no teeth, or the base cannot be compared)."""
        return self.kind != TEETH


def _chain(run: ProbeRun) -> list[bool]:
    """The env flag of each link in the runner's chain file; empty when the file is missing or
    does not end with this run's nonce (then it is not the runner's record)."""
    if not run.nonce or not run.chain:
        return []
    lines = run.chain.decode("utf-8", "replace").split("\n")
    if lines[-2:] != [f"{END} {run.nonce}", ""]:
        return []
    return [
        line[len(PREFIX) :].split(" ", 1)[0] == "1"
        for line in lines[:-2]
        if line.startswith(PREFIX)
    ]


def _differs(values: dict, expected: Expected, key: str) -> bool:
    if key not in values:
        return True
    return not same(values[key], expected.values[key], expected.tolerance.get(key))


def classify_base(run: ProbeRun, expected: Expected) -> BaseResult:
    """The base teeth class of one base run (see the module docstring)."""
    chain = _chain(run)
    if run.timed_out or run.survivors or run.returncode is None or run.returncode < 0:
        return BaseResult(UNCOMPARABLE, BASE_NO_OUTPUT)
    if run.returncode != 0:
        if not chain:
            return BaseResult(UNCOMPARABLE, BASE_NO_OUTPUT)
        return BaseResult(UNCOMPARABLE, BASE_ENV_ERROR) if any(chain) else BaseResult(TEETH)
    try:
        values, _raises = parse_document(run.output or b"")
    except Invalid:
        return BaseResult(UNCOMPARABLE, BASE_NO_OUTPUT)
    if any(_differs(values, expected, key) for key in strong_keys(expected)):
        return BaseResult(TEETH)
    return BaseResult(NO_TEETH)
