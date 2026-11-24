# plugin_select (#851 step 2)

Experimental: a static scan of the files a verifier run executes, and a selector that loads
only the table pytest plugins the run uses. Default off, not wired into the verifiers.

- `scanner.py`: reads the target files and their conftest.py files from git (base) and the
  candidate's own conftests, parses them with ast, and returns the identifiers they use.
  Anything it cannot classify (unparsable, absent, too large, async def, pytest_plugins,
  star or repository imports, dynamic calls) gives "unknown".
- `selector.py`: `plan()` returns the installed plugins to pass with -p. Unknown means all of
  them (the main behaviour). A table plugin is left out only when its names and ini keys are
  unused. `needs_full_rerun()` flags a failure that a missing plugin causes, so the caller
  reruns with every plugin.

Names come from `cairnwork.plugin_names`; ini keys from `cairnwork.pytest_config.PLUGINS`.
The asyncio row lives in `selector.py` because PLUGIN_NAMES has no asyncio row (changing it
changes attribution text, a step-0 change).

Not handled: addopts (dropped from the verifier ini by base_settings, so it cannot load
plugins); directory targets (they give unknown, so every plugin loads) and -k selection.
