"""Issue #1779: the machinery-word check matches exact names, not substrings.

Every case commits a base file and a candidate file that adds the given lines, then calls
``refuse_new_sys_path_mutation`` on the real change set. The base always carries an import-system
signal (``import inspect``), so only the statement check decides. MUST_PASS lines are honest edits
that main refused; MUST_BLOCK lines reach the import machinery or execute code by name and must
stay refused (they were refused on main too).
"""

import pytest
from conftest import git

from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.workspace import CandidateRejected

HEAD = "import re\nimport inspect\n\n"
TAIL = "def f():\n    pass\n"
RESOLVER = (
    "import importlib\nT = 'json:loads'\n"
    "def res(t):\n    m, _, a = t.partition(':')\n"
    "    return getattr(importlib.import_module(m), a)\n"
)
NAME = "pkg/mod.py"


def _verdict(tmp_path, base_extra, added, extra_files=()):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    (repo / "pkg").mkdir()
    base_text = HEAD + base_extra + TAIL
    (repo / NAME).write_text(base_text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / NAME).write_text(base_text + added)
    for name in extra_files:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("X = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    cand = git(repo, "rev-parse", "HEAD")
    try:
        refuse_new_sys_path_mutation(repo, base, cand, [NAME])
    except CandidateRejected as err:
        return err.code
    return None


MUST_PASS = [
    ("", "P = re.compile(r'x')\n"),
    ("", "def g():\n    return re.compile('x')\n"),
    ("import regex\n", "pattern = regex.compile(source)\n"),
    ("", "evaluate = 1\n"),
    ("", "executor = 1\n"),
    ("", "compiled = 1\n"),
    ("", "marshaller = 1\n"),
    ("", "breakpoints = []\n"),
    ("", "fut = loop.run_in_executor(None, f)\n"),
    ("", "v = getattr(obj, 'name', None)\n"),
    ("", "P = 1\n"),
    ("", "n = len(items)\n"),
    ("", "log = 'Starting model evaluation...'\n"),
    ("", "log = 'executing step 3'\n"),
    ("", "__all__ = ['evaluate', 'executor']\n"),
    ("", "def g():\n    '''Compile and evaluate the rules.'''\n    return 1\n"),
    # Controls that main refused and an exact check must keep passing.
    ("", "m = inspect.getmodule(obj)\nn = m.name\n"),
    ("import pydoc\n", "m = pydoc.locate(name)\nv = m.run(s)\n"),
    ("M = inspect.getmodule(len)\n", "n = M.__name__\n"),
    ("import asyncio\n", "t = asyncio.create_task(c)\n"),
    ("import json\n", "v = json.loads(s)\n"),
    ("def load2(f):\n    return f.read()\n", "v = load2(fh)\n"),
    (
        RESOLVER,
        "T = 'json:dumps'\n",
    ),
]

MUST_BLOCK = [
    ("", "v = getattr(obj, name, None)\n"),
    ("", "setattr(obj, name, v)\n"),
    ("", "code = compile(src, 'f', 'exec')\n"),
    ("", "v = eval(s)\n"),
    ("", "exec(s)\n"),
    ("", "fn = exec\n"),
    ("", "run(eval)\n"),
    ("", "g = globals()\n"),
    ("", "l = locals()\n"),
    ("", "v = vars(o)\n"),
    ("", "breakpoint()\n"),
    ("", "import builtins\nbuiltins.exec(s)\n"),
    ("", "__builtins__.compile(s, 'f', 'exec')\n"),
    ("", "v = __builtins__['exec']\n"),
    ("", "m = __import__('os')\n"),
    ("", "import importlib\nm = importlib.import_module('x')\n"),
    ("", "import builtins\nb = builtins\nb.compile(s, 'f', 'exec')\n"),
    ("import builtins\nb = builtins\n", "b.compile(s, 'f', 'exec')\n"),
    ("import builtins\nb = builtins\nc = b\n", "c.eval(s)\n"),
    ("", "f = exec\nf(s)\n"),
    ("", "from builtins import exec as x\n"),
    ("", "import builtins\nbuiltins.__dict__['ex' + 'ec'](s)\n"),
    ("", "import builtins\ngetattr(builtins, 'ex' + 'ec')(s)\n"),
    ("", "name = 'exec'\n"),
    ("", "import sys\nsys.modules['x'] = y\n"),
    ("", "import sys\nsetattr(sys, 'modules', d)\n"),
    ("", "import pickle\n"),
    ("", "from pickle import loads\n"),
    ("", "import marshal\n"),
    ("import pickle\n", "v = pickle.loads(b)\n"),
    ("", "import os\nos.execv(p, a)\n"),
    ("", "m = inspect.getmodule(print).exec(s)\n"),
    ("", "import pydoc\npydoc.locate('buil' + 'tins').eval(s)\n"),
    ("def get():\n    return builtins\n", "get().compile(s, 'f', 'exec')\n"),
    ("", "import _pickle\n"),
    ("", "import cloudpickle\n"),
    ("", "v = np.load(f, allow_pickle=True)\n"),
    ("", "v = pd.read_pickle(p)\n"),
    ("", "import py_compile\npy_compile.compile(p, cfile=c)\n"),
    ("", "import compileall\ncompileall.compile_dir(d)\n"),
    ("", "import sys\nsys.breakpointhook = hook\n"),
    ("", "from os import execv\n"),
    ("", "import os as o\no.execv(p, a)\n"),
    ("", "from pickle import *\n"),
    ("", "m = modules[n].exec(s)\n"),
    ("", "r = (lambda b: b.exec(p))(__builtins__)\n"),
    ("", "b = (lambda: __builtins__)()\n"),
    ("", "match __builtins__:\n    case b:\n        pass\n"),
    ("", "d = {}\nd[0] = __builtins__\n"),
    ("", "type Callback[T = eval] = list[T]\n"),
    ("", "s = f'{eval(x)}'\n"),
    ("", "n = 'exec'\n"),
    ("", "n = 'builtins.exec'\n"),
    ("", "n = b'eval'\n"),
    ("", "n = ' exec '\n"),
    ("", "n = '__builtins__.exec'\n"),
    ("", "msg = 'cache.pickle'\n"),
    ("x = getattr(o, NAME)\nNAME = 'a'\n", "NAME = 'exec'\n"),
    ("x = getattr(o, NAME)\nNAME = 'a'\n", "NAME = 'builtins.eval'\n"),
    ("import subprocess, sys\n", "subprocess.Popen([sys.executable, '-c', 'pass'])\n"),
    # Whole families: attribute on any receiver, word-split identifiers, strings, embedded names.
    ("", "m = inspect.getmodule(len)\nm.exec(s)\n"),
    ("", "m = inspect.getmodule(len)\nv = m.eval(s)\n"),
    ("import pydoc\n", "m = pydoc.locate(len.__module__)\nm.exec(s)\n"),
    ("", "def g(s, m=inspect.getmodule(len)):\n    return m.exec(s)\n"),
    ("M = inspect.getmodule(len)\n", "M.exec(s)\n"),
    ("import builtins\nsetattr(cfg, 'rt', builtins)\n", "cfg.rt.exec(s)\n"),
    ("from .compat import _bi\n", "_bi.exec(s)\n"),
    ("", "def run2(m, s):\n    return m.exec(s)\n"),
    ("import asyncio\n", "p = asyncio.create_subprocess_exec('python3', '-c', 'pass')\n"),
    ("import asyncio\n", "p = loop.subprocess_exec(proto, 'python3')\n"),
    ("import jsonpickle\n", "v = jsonpickle.decode(s)\n"),
    ("from pickle import Unpickler\n", "v = Unpickler(f).load()\n"),
    (
        "from pickle import Unpickler\ndef load(f):\n    return Unpickler(f).load()\n",
        "v = load(fh)\n",
    ),
    (
        RESOLVER,
        "T = 'pickle:loads'\n",
    ),
    ("", "T = 'marshal:loads'\n"),
    ("", "T = 'pickle.'\n"),
    ("", "T = 'x/exec'\n"),
    ("import codeop\n", "c = codeop.compile_command(s)\n"),
    ("import execnet\n", "g = execnet.makegateway()\n"),
    ("import distutils.ccompiler\n", "cc = distutils.ccompiler.new_compiler()\n"),
    ("", "def h():\n    global eval\n    eval = hook\n"),
    ("import builtins\n", "v = getattr(builtins, bytes([101,120,101,99]).decode())\n"),
    ("import builtins as b\n", "b.exec(s)\n"),
    ("import builtins\n", "v = getattr(builtins, 'exec')\n"),
    ("import operator\n", "f = operator.attrgetter('ex' 'ec')\n"),
    ("import functools\n", "f = functools.partial(eval, s)\n"),
    ("import types\n", "f = types.FunctionType(c, {})\n"),
    ("", "f = globals()['__builtins__']\n"),
    ("", "m = len.__self__\n"),
    ("import runpy\n", "runpy.run_path(p)\n"),
    ("", "if (e := eval):\n    pass\n"),
    ("", "@eval\ndef k():\n    pass\n"),
    ("import builtins\nclass K:\n    rt = builtins\n", "K.rt.exec(s)\n"),
    ("import builtins\nmatch builtins:\n    case bb:\n        pass\n", "bb.exec(s)\n"),
    ("", "n = 'ex' 'ec'\n"),
    ("", "n = r'exec'\n"),
    ("import builtins\nfor bq in [builtins]:\n    pass\n", "bq.exec(s)\n"),
    ("import builtins\nwith ctx(builtins) as bw:\n    pass\n", "bw.exec(s)\n"),
    ("import os as o\n", "o.execvpe(p, a, e)\n"),
    ("import subprocess, sys\n", "subprocess.Popen([sys._base_executable])\n"),
    ("", "x: 'eval' = 1\n"),
    ("", "def q[T = eval]():\n    pass\n"),
]

# Honest-looking edits that stay refused (listed as known false refusals in the PR): an attribute
# with a machinery word is a hit on any receiver, and an identifier hits on any word of it.
KNOWN_REFUSED = [
    "v = df.eval('a + b')\n",
    "x = obj.exec(1)\n",
    "vars_count = 1\n",
    "y = z.globals_ok\n",
    "def compile_rules():\n    return 1\n",
    "v = ast.literal_eval(s)\n",
]

# The whitelisted re.compile / regex.compile loses its pass when the name can be something else.
UNTRUSTED_RE = [
    ("", "re = obj\nP = re.compile(x)\n", ()),
    ("", "def g(re):\n    return re.compile(x)\n", ()),
    ("", "for re in mods:\n    P = re.compile(x)\n", ()),
    ("", "with ctx() as re:\n    P = re.compile(x)\n", ()),
    ("", "global re\nP = re.compile(x)\n", ()),
    ("", "del re\nP = re.compile(x)\n", ()),
    ("from os import *\n", "P = re.compile(x)\n", ()),
    ("", "match obj:\n    case re:\n        pass\nP = re.compile(x)\n", ()),
    ("", "P = re.compile(x)\n", ("pkg/re.py",)),
    ("", "P = re.compile(x)\n", ("pkg/re/__init__.py",)),
    ("import regex\n", "P = regex.compile(x)\n", ("regex.py",)),
    ("", "P = re.exec(x)\n", ()),
    ("", "P = re.compile_all(x)\n", ()),
    ("", "import os as re\nP = re.compile(x)\n", ()),
]


@pytest.mark.parametrize(("base_extra", "added"), MUST_PASS)
def test_honest_edit_next_to_a_signal_is_not_refused(tmp_path, base_extra, added):
    assert _verdict(tmp_path, base_extra, added) is None


@pytest.mark.parametrize(("base_extra", "added"), MUST_BLOCK)
def test_machinery_by_name_stays_refused(tmp_path, base_extra, added):
    assert _verdict(tmp_path, base_extra, added) == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize("added", KNOWN_REFUSED)
def test_known_false_refusals_are_pinned(tmp_path, added):
    assert _verdict(tmp_path, "", added) == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(("base_extra", "added", "extra_files"), UNTRUSTED_RE)
def test_re_compile_pass_needs_a_plain_unshadowed_import(tmp_path, base_extra, added, extra_files):
    assert _verdict(tmp_path, base_extra, added, extra_files) == "CANDIDATE_SYS_PATH_MUTATION"


def test_aliased_plain_import_of_re_keeps_the_pass(tmp_path):
    assert _verdict(tmp_path, "import re as r\n", "P = r.compile(x)\n") is None
