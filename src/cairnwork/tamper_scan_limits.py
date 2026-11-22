"""The residual classes of tamper_scan (#1039): what its static scan does not catch, and what then.

The scan in tamper_scan.py is a hurdle, not a boundary. Each class below passes it. For each: the
tracking Issues; the run-time defence that refuses the class (``src/`` files), or ``none``; and
the pinning tests, one node id per ``Pin:`` line. A ``none`` class is pinned only by the level
label: a pytest verdict stays ``same_process_only`` (protection.py), so no protection beyond
the same-process hurdle is claimed for it. Categories only: no bypass shape is written here.
tests/test_tamper_scan_limits.py keeps this list parseable and every pin real.

Class 1: names built at run time to reach a pytest-internal module, class or attribute, to change
what pytest reports.
  Issues: #571, #643, #800
  Defence: none (src/cairnwork/report_guard.py: "hurdles, not a boundary")
  Pin:
  tests/test_protection.py::test_levels

Class 2: names built at run time to reach the reporter instance, report objects or the JSON encoder.
  Issues: #571, #643
  Defence: none (the import-time binding in src/cairnwork/report_guard.py only stops rebinding of
  module attributes)
  Pin:
  tests/test_protection.py::test_levels

Class 3: redirecting the bytecode location and planting bytecode.
  Issues: #276
  Defence: after-run scans (src/cairnwork/pycache_guard.py, src/cairnwork/bytecode_roots.py), an
  audit hook refusing bytecode reads and unmarshal (src/cairnwork/bytecode_hook.py), and on macOS
  a Seatbelt read deny (src/cairnwork_sandbox/seatbelt.py). The Linux pins in
  tests/test_pycache_redirect.py are account-only, so Linux relies on the scans and the hook.
  Pin:
  tests/test_seatbelt_pyc.py::test_bytecode_under_a_write_root_cannot_be_read_by_any_spelling
  tests/test_bytecode_hook.py::test_the_hook_holds_after_each_step
  Scan-only (these show the static scan refuses the redirect before the run; not run-time cover):
  tests/test_pycache_redirect_spellings.py::test_a_redirect_built_from_strings_is_refused_for_sys_path_mutation
  tests/test_pycache_redirect_spellings.py::test_bytecode_removed_before_the_run_ends_is_not_verified

Class 4: early exit, interpreter object-graph walks or frame dumps, by rebinding, split lines or
context-line aliases.
  Issues: #611
  Defence: a run with no record fails (src/cairnwork/report_guard.py); the hook refuses the
  object-graph and frame-dump events (src/cairnwork/bytecode_hook.py)
  Pin:
  tests/test_report_guard.py::test_os_exit_before_sessionfinish_leaves_no_record
  tests/test_bytecode_hook.py::test_the_hook_holds_after_each_step

Class 5: writes to the module table with a variable key, key variants or a swapped mapping.
  Issues: #303, #276
  Defence: only the narrow frozen-stdlib-name check (src/cairnwork/bytecode_hook.py); otherwise
  none
  Pin:
  tests/test_bytecode_hook.py::test_a_frozen_module_name_held_by_another_module_fails_the_install
  tests/test_protection.py::test_levels

Class 6: import-path changes made mid-run (search path, finders, path hooks, .pth, archives, a
non-.py file imported later).
  Issues: #800, #276
  Defence: none at run time. Only the start-up inputs are protected, as the pins show.
  Pin:
  tests/test_protection.py::test_levels
  tests/test_root_modules.py::test_new_root_module_and_pth_are_refused
  tests/test_candidate_shadow_refusals.py::test_a_task_naming_a_file_that_shadows_pytest_startup_or_stdlib_is_refused

Class 7: skipping, deselecting or renaming named tests.
  Issues: #643, #800
  Defence: a named test that did not run means not verified (src/cairnwork/verdict_record.py,
  not_run; src/cairnwork/report_guard.py records the node id per item)
  Pin:
  tests/test_file_part_sites_1272.py::test_verdict_owns_a_test_of_a_target_in_a_path_with_double_colons

Class 8: forging the record by reading the nonce or path.
  Issues: #276
  Defence: partial: an environment scrub and the nonce pipe closed early
  (src/cairnwork/report_guard.py calls it a partial mitigation)
  Pin:
  tests/test_report_guard.py::test_the_descriptor_hand_over_records_a_passing_run_and_scrubs_the_environment
  tests/test_report_guard.py::test_the_nonce_pipe_is_closed_by_the_plugin_before_later_plugins_load
  tests/test_report_guard.py::test_a_record_with_another_nonce_in_the_report_descriptor_is_not_a_pass
"""
