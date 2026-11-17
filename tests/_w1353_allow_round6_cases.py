"""Round-six edits aimed at trusted witness helper bodies."""

ROUND_SIX_MUTATIONS = {
    "probe_module_builtin_str": (
        "tests/_w1353_probe.py",
        '\ndef str(x): return "PASS"\n',
    ),
    "probe_verify_literal_return": (
        "tests/_w1353_probe.py",
        "def verify(",
    ),
    "probe_scrub_literal_return": (
        "tests/_w1353_probe.py",
        "def _scrub(",
    ),
    "extra_rows_fake_path": (
        "tests/_w1353_extra.py",
        'Path(__file__).with_name("_w" + "1353_c10.py")',
    ),
    "c10_candidate_source_pass": (
        "tests/_w1353_c10.py",
        '"def add(a, b): return a + b"',
    ),
    "c10_sys_modules_update": (
        "tests/_w1353_c10.py",
        'sys.modules.update({"_cairnwork_blackbox_runner": runner})',
    ),
}


def mutate_case(source, case_id):
    """Apply one executable mutation to source text for the named case."""
    if case_id == "probe_module_builtin_str":
        return source + ROUND_SIX_MUTATIONS[case_id][1]
    if case_id == "probe_verify_literal_return":
        start = source.index("def verify(")
        line_end = source.index("\n", start)
        indent = len(source[line_end + 1 :]) - len(source[line_end + 1 :].lstrip())
        return source[: line_end + 1] + " " * indent + 'return "PASS"\n' + source[line_end + 1 :]
    if case_id == "probe_scrub_literal_return":
        start = source.index("def _scrub(")
        line_end = source.index("\n", start)
        indent = len(source[line_end + 1 :]) - len(source[line_end + 1 :].lstrip())
        return source[: line_end + 1] + " " * indent + 'return "PASS"\n' + source[line_end + 1 :]
    if case_id == "extra_rows_fake_path":
        old = 'Path(__file__).with_name("_w" + "1353_c10.py")'
        return source.replace(old, 'parent / "fake.py"', 1)
    if case_id == "c10_candidate_source_pass":
        old = '"import sys\\ndef add(a,b):\\n if " + condition + ": return a+b\\n return a-b\\n"'
        return source.replace(old, "\"def add(a,b): return 'PASS'\\n\"", 1)
    if case_id == "c10_sys_modules_update":
        return source.replace(
            'sys.modules.update({"_cairnwork_blackbox_runner": runner})',
            'sys.modules.update({"cairnwork.blackbox_judge": runner})',
            1,
        )
    raise KeyError(case_id)
