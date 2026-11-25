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
NAME = "pkg/mod.py"


def _verdict(tmp_path, base_extra, added):
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
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    cand = git(repo, "rev-parse", "HEAD")
    try:
        refuse_new_sys_path_mutation(repo, base, cand, [NAME])
    except CandidateRejected as err:
        return err.code
    return None


MUST_PASS = [
    "P = re.compile(r'x')\n",
    "def g():\n    return re.compile('x')\n",
    "pattern = regex.compile(source)\n",
    "evaluate = 1\n",
    "executor = 1\n",
    "compiled = 1\n",
    "marshaller = 1\n",
    "breakpoints = []\n",
    "y = z.globals_ok\n",
    "def compile_rules():\n    return 1\n",
    "v = ast.literal_eval(s)\n",
    "v = df.eval('a + b')\n",
    "fut = loop.run_in_executor(None, f)\n",
    "vars_count = 1\n",
    "v = getattr(obj, 'name', None)\n",
    "P = 1\n",
    "x = obj.exec(1)\n",
    "n = len(items)\n",
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
]


@pytest.mark.parametrize("added", MUST_PASS)
def test_honest_edit_next_to_a_signal_is_not_refused(tmp_path, added):
    assert _verdict(tmp_path, "", added) is None


@pytest.mark.parametrize(("base_extra", "added"), MUST_BLOCK)
def test_machinery_by_name_stays_refused(tmp_path, base_extra, added):
    assert _verdict(tmp_path, base_extra, added) == "CANDIDATE_SYS_PATH_MUTATION"


# Gemini design review: the statement that introduces the handle names the builtins itself, so it
# is hot on its own; these must stay refused even though the later use has no builtins word.
MUST_BLOCK += [
    ("", "r = (lambda b: b.exec(p))(__builtins__)\n"),
    ("", "b = (lambda: __builtins__)()\n"),
    ("", "match __builtins__:\n    case b:\n        pass\n"),
    ("", "d = {}\nd[0] = __builtins__\n"),
    ("", "type Callback[T = eval] = list[T]\n"),
    ("", "s = f'{eval(x)}'\n"),
]


# Owner ruling on string literals: only a whole-string machinery word or a dotted path with one
# such segment counts; prose, log lines and docstrings that merely contain a word do not.
MUST_PASS += [
    "log = 'Starting model evaluation...'\n",
    "log = 'executing step 3'\n",
    "__all__ = ['evaluate', 'executor']\n",
    "def g():\n    '''Compile and evaluate the rules.'''\n    return 1\n",
]
MUST_BLOCK += [
    ("", "n = 'exec'\n"),
    ("", "n = 'builtins.exec'\n"),
    ("", "n = b'eval'\n"),
    ("", "n = ' exec '\n"),
    ("", "n = '__builtins__.exec'\n"),
    ("", "msg = 'cache.pickle'\n"),
    ("x = getattr(o, NAME)\nNAME = 'a'\n", "NAME = 'exec'\n"),
    ("x = getattr(o, NAME)\nNAME = 'a'\n", "NAME = 'builtins.eval'\n"),
]


# Found by the existing child-process attack test: the interpreter path is how a candidate starts
# a child that outlives the run, and main refused it only through the word "exec" inside it.
MUST_BLOCK += [
    ("import subprocess, sys\n", "subprocess.Popen([sys.executable, '-c', 'pass'])\n"),
]
