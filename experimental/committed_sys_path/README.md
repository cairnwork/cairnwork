# committed_sys_path (Cairnwork Experimental Capability)

## Threat Model
In Cairnwork candidate validation, malicious or flawed patches may manipulate Python module resolution to escape sandboxing or inject unauthorized code during execution. Previous attempts relied on write-side deny-lists (e.g. blocking specific file open operations), which failed to converge as candidates continually invented new writing techniques (`builtins.open`, `functools.partial`, indirect containers `[open][0]`, `bz2.BZ2File`, `os.system`, cross-file writes).

`committed_sys_path` shifts defence from the write-side to the search-path resolution side: regardless of how a file is written, module import hijacking requires directing Python's search path to an attacker-controlled directory. Therefore, any in-process search-path modification introduced by a candidate commit must statically resolve to a repository-internal directory that already existed in the base commit, and must not point to directories where the candidate added or modified potential shadow modules.

## Scope
- **Narrow In-Process Search Path Edits**: Tracks modifications to `sys.path` (`.insert()`, `.append()`, `.extend()`, `sys.path[:] = ...`, `sys.path += ...`), `site.addsitedir(...)`, `__path__` modifications, and `submodule_search_locations` edits.
- **`sys` and `sys.path` Alias Tracking**: Analyzes AST to trace aliases and expressions yielding `sys` or `sys.path` (e.g. `import sys as s`, `from os import sys`, `os.sys`, `m = [sys][0]`, `m = sys if c else None`, `(m := sys)`, `def g(): return sys; g()`, `getattr(sys, 'path')`, `importlib.import_module('sys')`, `__import__('sys')`, default parameters `def f(m=sys):`, and tuple/list destructuring).
- **Static Target Evaluation**: Statically resolves relative paths rooted at `__file__`, `os.path.dirname`, `abspath`, `realpath`, `join`, `Path(__file__).resolve().parent` / `.parents[k]`, `/` operator, `joinpath`, `str`, `fspath`, same-file constant aliases (`BIN`, `HERE`, `ROOT`), and literal relative paths.
- **Base Committed & Shadow Verification**: Verifies target directory exists in `base` committed tree. Rejects if directory is uncommitted, escapes checkout, or contains candidate added/modified `.py` modules (shadow modules).
- **Scope Control**: Non-test source files only by default (`tests/` and `conftest.py` excluded by default); explicit allowlist support (e.g. `experimental/`).

## Activation
This module is experimental and **disabled by default**. To activate:
Set the environment variable:
```bash
export CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH=1
```
Without this variable set, invoking the CLI or entrypoint exits immediately with code 2.

## Known Limitations & Out-of-Scope
- **Non-Path Modifications**: Attack vectors that do not alter the search path are out of scope for this module:
  1. `sys.modules` manipulation (e.g. `sys.modules['calc'] = fake`), which is handled by existing core machinery checks (`candidate_sys_path.py` `_MACHINERY`).
  2. Direct writes to existing `cwd` / script directories or `site-packages` without altering search paths.
- **Subprocess Environment**: Does not flag subprocess invocations passing `env={"PYTHONPATH": ...}` or shell configuration scripts, as these represent external process configurations rather than in-process sandbox escapes.
