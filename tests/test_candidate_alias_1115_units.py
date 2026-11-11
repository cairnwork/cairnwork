"""Direct unit tests for candidate_alias pure functions (#1115)."""

from __future__ import annotations

import pytest

from cairnwork.candidate_alias import alias_machinery_changed, runtime_shadow_added


def test_alias_machinery_changed_added_swap():
    assert alias_machinery_changed(None, b"import sys as s\ns.modules['a'] = 1\n") is True


def test_alias_machinery_changed_unchanged_file():
    assert alias_machinery_changed(b"x = 1\n", b"x = 1\n") is False


def test_alias_machinery_changed_tracks_binding_forms_and_any_receiver_tables():
    sources = (
        "import sys\nm: object = sys\nm.modules['x'] = 1\n",
        "import sys\na, m = 1, sys\nm.modules['x'] = 1\n",
        "import sys\nfor m in (sys,):\n    m.modules['x'] = 1\n",
        "import sys\nwith sys as m:\n    m.modules['x'] = 1\n",
        "import sys\ndef f(m=sys):\n    m.modules['x'] = 1\n",
        "other.meta_path.append(finder)\n",
    )
    assert all(alias_machinery_changed(None, source.encode()) for source in sources)
    assert (
        alias_machinery_changed(
            None, b"class Service:\n    def run(self):\n        self.modules()\n"
        )
        is False
    )


def test_sys_star_import_binds_all_table_names():
    assert alias_machinery_changed(None, b"from sys import *\nmodules['x'] = 1\n") is True


def test_runtime_shadow_added_open_write_and_path_insert():
    src = b"import sys\nopen('f', 'w').write('x')\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is True


def test_runtime_shadow_added_open_default_read_mode_is_false():
    src = b"import sys\nopen('f')\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is False


def test_runtime_shadow_added_open_non_literal_mode_is_true():
    src = b"import sys\nopen('f', mode)\nsys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(None, src) is True


def test_runtime_shadow_added_detects_method_and_helper_writes():
    for write in (
        "Path('f').open('w')",
        "target.write('x')",
        "target.writelines(lines)",
        "archive.writestr('f', data)",
        "NamedTemporaryFile(mode=mode)",
        "FileIO('f', mode)",
        "copyfileobj(src, dst)",
    ):
        source = f"import sys\n{write}\nsys.path.insert(0, 'd')\n".encode()
        assert runtime_shadow_added(None, source), write
    assert (
        runtime_shadow_added(None, b"import sys\nPath('f').open()\nsys.path.insert(0, 'd')\n")
        is False
    )


def test_runtime_shadow_added_combines_added_signal_with_base_state_signal():
    old = b"import sys\nsys.path.insert(0, 'd')\nopen('f', 'w')\n"
    new_write_added = old + b"open('g', 'w')\n"
    assert runtime_shadow_added(old, new_write_added) is True

    old = b"import sys\nopen('f', 'w')\n"
    new_path_added = old + b"sys.path.insert(0, 'd')\n"
    assert runtime_shadow_added(old, new_path_added) is True


def test_unparseable_new_raises_value_error():
    with pytest.raises(ValueError):
        alias_machinery_changed(None, b"def (syntax error")
    with pytest.raises(ValueError):
        runtime_shadow_added(None, b"def (syntax error")


@pytest.mark.parametrize(
    "call",
    [
        "gzip.open('f.gz', 'w')",
        "codecs.open('f', 'w')",
        "bz2.open(path, 'a')",
        "g.open('data/raw.gz', 'wb')",
        "g.open('data/raw.gz', mode)",
        "gzip.open('w')",
        "gzip.open(path, mode)",
        "Path('f').open('a')",
        "p.open(mode)",
    ],
)
def test_runtime_shadow_added_open_write_through_module_and_path_receivers(call):
    src = f"import sys\n{call}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is True


@pytest.mark.parametrize(
    "call", ["gzip.open('data/raw.gz')", "gzip.open(path, 'rb')", "Path('f').open('r', 1)"]
)
def test_runtime_shadow_added_open_read_through_module_and_path_receivers(call):
    src = f"import sys\n{call}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is False


@pytest.mark.parametrize(
    "prelude, write",
    [
        ("import io\n", "io.StringIO().write('x')"),
        ("import io\n", "io.BytesIO(b'').write(b'x')"),
        ("import io\nbuf = io.StringIO()\n", "buf.write('x')"),
        ("from io import StringIO\n", "StringIO().writelines(['x'])"),
        ("import io\n", "with io.StringIO() as buf:\n    buf.write('x')"),
    ],
)
def test_runtime_shadow_added_in_memory_stream_write_is_not_a_file_write(prelude, write):
    # Codex R2 on #1176: io.StringIO().write() next to a sys.path edit must not be refused
    src = f"import sys\n{prelude}{write}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is False


@pytest.mark.parametrize(
    "source",
    [
        # the name is also bound to something else
        "import io\nbuf = io.StringIO()\nbuf = open_file()\nbuf.write('x')\n",
        "import io\nbuf = io.StringIO()\ndef f(buf):\n    buf.write('x')\n",
        # io or the constructor names may be rebound
        "import os as io\nbuf = io.StringIO()\nbuf.write('x')\n",
        "import io\nio = handle\nio.StringIO().write('x')\n",
        "from io import StringIO\nStringIO = factory\nStringIO().write('x')\n",
        "from mylib import StringIO\nStringIO().write('x')\n",
        "from io import StringIO as S\nS().write('x')\n",
        "import io\nio.StringIO = factory\nio.StringIO().write('x')\n",
        "import io\nsetattr(io, 'StringIO', factory)\nio.StringIO().write('x')\n",
        # unknown receivers stay file writes
        "f = os.fdopen(fd, 'w')\nf.write('x')\n",
        "self.buf.write('x')\n",
    ],
)
def test_runtime_shadow_added_write_on_untrusted_receiver_stays_refused(source):
    src = f"import sys\n{source}sys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is True


@pytest.mark.parametrize("call", ["open(*a)", "open(**k)", "p.open(**k)", "io.open(*a)"])
def test_runtime_shadow_added_open_with_unpacked_arguments_fails_closed(call):
    src = f"import sys\n{call}\nsys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is True


@pytest.mark.parametrize(
    "source",
    [
        # security review on 4fa7013: the trusted object or io namespace is changed after binding
        "import io\nbuf = io.StringIO()\nbuf.write = sink.write\nbuf.write('x')\n",
        "import io\nbuf = io.StringIO()\npatch(buf)\nbuf.write('x')\n",
        "import io\nbuf = io.StringIO()\nalias = buf\nbuf.write('x')\n",
        "import io\nio.__dict__['StringIO'] = factory\nio.StringIO().write('x')\n",
        "import io\nsys.modules['io'] = fake\nio.StringIO().write('x')\n",
        "import io\nns['StringIO'] = factory\nio.StringIO().write('x')\n",
        "import io\npatch(io)\nio.StringIO().write('x')\n",
        "from io import StringIO\npatch(StringIO)\nStringIO().write('x')\n",
    ],
)
def test_runtime_shadow_added_mutated_memory_stream_is_not_trusted(source):
    src = f"import sys\n{source}sys.path.insert(0, 'd')\n".encode()
    assert runtime_shadow_added(None, src) is True
