"""#854 (#797 step 3): a denial-shaped OSError (EACCES, EPERM, EROFS, ENETUNREACH) inside a
failure says "environment" too: an exception group member, a ``__cause__``, an unsuppressed
``__context__``, at any depth. Before, only the outermost exception was looked at, so a sandbox
denial a test re-raised inside a group gave teeth and a wrong candidate could be VERIFIED. Other
wrapped OSErrors (a missing file the code turned into its own error) stay a plain failure, as on
main, and so does a denial the test caught and handled. End to end through the real plugin."""

from test_fail_kind_guard import T, _run

ENV = (
    "import errno\n"
    "def denied():\n"
    "    return PermissionError(errno.EPERM, 'Operation not permitted')\n"
    "def test_group():\n"
    "    raise ExceptionGroup('setup', [ValueError('x'), denied()])\n"
    "def test_cause():\n"
    "    raise ValueError('could not read') from denied()\n"
    "def test_context():\n"
    "    try:\n"
    "        raise denied()\n"
    "    except PermissionError:\n"
    "        raise RuntimeError('reading failed')\n"
    "def test_nested():\n"
    "    inner = ExceptionGroup('b', [ExceptionGroup('c', [OSError(errno.EROFS, 'ro')])])\n"
    "    raise ExceptionGroup('a', [KeyError('k'), inner])\n"
    "def test_nested_eperm():\n"
    "    inner = ExceptionGroup('c', [ValueError('v'), denied()])\n"
    "    raise ExceptionGroup('a', [ExceptionGroup('b', [KeyError('k'), inner])])\n"
    "def test_eacces_in_cause_of_a_member():\n"
    "    member = ValueError('m')\n"
    "    member.__cause__ = OSError(errno.EACCES, 'no')\n"
    "    raise ExceptionGroup('a', [member])\n"
    "def test_network_unreachable():\n"
    "    raise ExceptionGroup('fetch', [OSError(errno.ENETUNREACH, 'no network')])\n"
    "def test_past_the_bound():\n"
    "    err = ValueError(0)\n"
    "    for n in range(1, 80):\n"
    "        err, err.__cause__ = ValueError(n), err\n"
    "    raise err\n"
)

PLAIN = (
    "import errno\n"
    "def test_group_of_plain_errors():\n"
    "    raise ExceptionGroup('bug', [AssertionError('a'), ValueError('b')])\n"
    "def test_a_denial_caught_and_handled():\n"
    "    try:\n"
    "        open('/nonexistent-dir/x', 'w')\n"
    "    except OSError:\n"
    "        pass\n"
    "    raise ExceptionGroup('bug', [AssertionError('wrong total')])\n"
    "def test_own_error_from_a_missing_file():\n"
    "    try:\n"
    "        raise FileNotFoundError(errno.ENOENT, 'no config')\n"
    "    except FileNotFoundError:\n"
    "        raise LookupError('config not found')\n"
    "def test_context_suppressed_by_the_test():\n"
    "    try:\n"
    "        raise PermissionError(errno.EPERM, 'denied')\n"
    "    except PermissionError:\n"
    "        raise AssertionError('not the cause') from None\n"
    "def test_connection_reset_is_not_a_denial():\n"
    "    raise ExceptionGroup('fetch', [OSError(errno.ECONNRESET, 'reset')])\n"
    "def test_a_cycle_ends():\n"
    "    a, b = ValueError('a'), ValueError('b')\n"
    "    a.__context__, b.__context__ = b, a\n"
    "    raise a\n"
)


def test_a_wrapped_denial_is_env_at_any_depth_and_past_the_bound(tmp_path):
    names = (
        "group",
        "cause",
        "context",
        "nested",
        "nested_eperm",
        "eacces_in_cause_of_a_member",
        "network_unreachable",
        "past_the_bound",
    )
    v = _run(tmp_path, ENV)
    assert v.outcomes == {f"{T}test_{n}": "failed" for n in names}
    assert v.fail_kind == {f"{T}test_{n}": "env" for n in names}


def test_plain_failures_and_other_wrapped_oserrors_keep_their_teeth(tmp_path):
    v = _run(tmp_path, PLAIN)
    assert set(v.outcomes.values()) == {"failed"} and len(v.outcomes) == 6
    assert v.fail_kind == {}


SIZES = (
    "import errno\n"
    "def chain(n, last):\n"  # n distinct exceptions linked by __cause__
    "    err = last\n"
    "    for k in range(n - 1):\n"
    "        err, err.__cause__ = ValueError(k), err\n"
    "    return err\n"
    "def group(n, last):\n"  # the group itself plus n - 1 members
    "    return ExceptionGroup('g', [ValueError(k) for k in range(n - 2)] + [last])\n"
    "def eperm():\n"
    "    return PermissionError(errno.EPERM, 'denied')\n"
    + "".join(
        f"def test_{shape}_{n}_{kind}():\n"
        f"    raise {shape}({n}, {'eperm()' if kind == 'eperm' else 'ValueError(-1)'})\n"
        for shape in ("chain", "group")
        for n in (63, 64, 65)
        for kind in ("plain", "eperm")
    )
)


def test_the_bound_counts_distinct_exceptions_so_64_plain_ones_keep_their_teeth(tmp_path):
    # James 11/05 (#882 independent check): 64 plain exceptions said env, from the empty
    # cause slots left after the 64th; 65 is past the bound (env, fail closed)
    v = _run(tmp_path, SIZES)
    env = {
        f"{T}test_{s}_{n}_{k}"
        for s in ("chain", "group")
        for n in (63, 64, 65)
        for k in ("plain", "eperm")
        if k == "eperm" or n == 65
    }
    assert len(v.outcomes) == 12 and set(v.outcomes.values()) == {"failed"}
    assert v.fail_kind == dict.fromkeys(env, "env")
