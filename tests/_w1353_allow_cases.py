"""Compact source cases for the closed witness grammar."""

P = "from cairnwork.blackbox_judge import judge_run\nfrom _w1353_probe import verify, row\n"

MUST_BLOCK = [
    'R="blackbox:add: PASS [matched 1/1]"\nfrom _w1353_probe import row\nrow("C9","t","r",R,"x","y")',  # noqa: E501
    'from _w1353_probe import row\ndef f():\n r="PASS"\n return row("C9","t","r",r,"x","y")',
    'from _w1353_probe import row\nrow("C9","t","r","PASS" if flag else "FAIL","x","y")',
    'from _w1353_probe import row\nrow("C9","t","r","PA"+"SS","x","y")',
    'from _w1353_probe import row\nrow("C9","t","r","".join(["PASS"]),"x","y")',
    'from _w1353_probe import row\nrow("C9","t","r",f"{\'PASS\'}","x","y")',
    'import _w1353_probe as probe\nprobe.row("C9","t","r","PASS","x","y")',
    'from _w1353_probe import row as rec\nrec("C9","t","r","PASS","x","y")',
    'observed="VER"+"IFIED"',
    'from _w1353_probe import row\nrow("C9","timeout","...","blackbox:add: VERIFICATION_INCOMPLETE: TIMEOUT","x","y")',  # noqa: E501
    'observed="PASS"',
    # Attribute and dynamic call-target bypasses.
    P + 'import _w1353_probe as p\ngetattr(p,"ro"+"w")("C9","t","r","PASS","x","y")',
    P + 'import _w1353_probe as p\nvars(p)["row"]("C9","t","r","PASS","x","y")',
    P + 'import _w1353_probe as p\np.__dict__["row"]("C9","t","r","PASS","x","y")',
    P
    + 'import operator, _w1353_probe as p\noperator.attrgetter("row")(p)("C9","t","r","PASS","x","y")',  # noqa: E501
    P
    + 'import importlib\ngetattr(importlib.import_module("_w1353_probe"),"r"+"ow")("C9","t","r","PASS","x","y")',  # noqa: E501
    P + 'import _w1353_probe as p\np.__getattribute__("row")("C9","t","r","PASS","x","y")',
    P + 'row(*["C9","t","r","PASS"],"x","y",judge_run(1))',
    P + 'row("C9","t","r",judge_run(1).get("zz","PASS"),"x","y")',
    P + 'row("C9","t","r",judge_run(1).get("status","PASS"),"x","y")',
    P + 'args=("C9","t","r","PASS","x","y")\nrow(*args)',
    P + 'r=judge_run(1)\nr.__setitem__("status","PASS")\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\ndict.update(r,status="PASS")\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nvars(r).update(status="PASS")\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nobject.__setattr__(r,"status","PASS")\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nr.clear()\nrow("C9","t","r",r.get("status","PASS"),"x","y")',
    P
    + 'def f():\n r=judge_run(1)\n def g():\n  nonlocal r\n  r="PASS"\n g()\n return row("C9","t","r",r,"x","y")\nf()',  # noqa: E501
    P + 'r=judge_run(1)\nglobals()["r"]="PASS"\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nexec("r=\\\'PASS\\\'")\nrow("C9","t","r",r,"x","y")',
    'from cairnwork.pipeline import TaskRejected\nfrom _w1353_probe import row\ntry:\n raise TaskRejected("PASS")\nexcept TaskRejected as exc:\n r=exc.args[0]\nrow("C9","t","r",r,"x","y")',  # noqa: E501
    'from cairnwork.pipeline import TaskRejected\nfrom _w1353_probe import row\nTaskRejected=ValueError\ntry:\n raise ValueError("PASS")\nexcept TaskRejected as exc:\n r=exc.args[0]\nrow("C9","t","r",r,"x","y")',  # noqa: E501
    'from cairnwork.module_inventory import _run\nfrom _w1353_probe import row\nrow("C9","t","r",_run("python","print(1)"),"x","y")',  # noqa: E501
    'from cairnwork.post_verified import _run\nfrom _w1353_probe import row\nrow("C9","t","r",_run(["echo","PASS"],5,{}),"x","y")',  # noqa: E501
    'rec=dict([("obs"+"erved","PASS"),("raw_return","PASS")])',
    'rec={}\nrec["obs"+"erved"]="PASS"',
    'rec={}\nrec.__setitem__("observed","PASS")',
    'import json\nrec=json.loads("{\\"observed\\":\\"PASS\\"}")',
    'def make():\n def row(*a):\n  return dict([("obs"+"erved","PASS")])\n return row("C9","t","r",None,"x","y")',  # noqa: E501
    "class row:\n def __new__(cls,*a):\n  return {}",
    P + "a,b=row,1",
    P + "a,b=judge_run(1),row",
    P
    + "exec(compile(\"row(\\'C9\\',\\'t\\',\\'r\\',\\'PASS\\',\\'x\\',\\'y\\')\",\"x\",\"exec\"))",
    P
    + 'import sys\nsys.modules["cairnwork.blackbox_judge"].judge_run=lambda *a:"PASS"\nrow("C9","t","r",judge_run(1),"x","y")',  # noqa: E501
    'import cairnwork.blackbox_judge as bj\nfrom _w1353_probe import row\nbj.judge_run=lambda *a:"PASS"\nrow("C9","t","r",bj.judge_run(1),"x","y")',  # noqa: E501
    'import cairnwork.blackbox_judge as bj\nfrom _w1353_probe import row\nsetattr(bj,"judge_run",lambda *a:"PASS")\nrow("C9","t","r",bj.judge_run(1),"x","y")',  # noqa: E501
    P
    + 'import functools\n@functools.cache\ndef f(): return judge_run(1)\nrow("C9","t","r",f(),"x","y")',  # noqa: E501
    'import os\nif os.environ.get("X"):\n from cairnwork.blackbox_judge import judge_run\nelse:\n from cairnwork_evil import judge_run\nfrom _w1353_probe import row\nrow("C9","t","r",judge_run(1),"x","y")',  # noqa: E501
    # Every extra binding poisons names; exact package and import level are required.
    P
    + 'result=verify(1)\ndef emit(result):\n return row("C9","t","r",result,"x","y")\nemit("PASS")',
    P + 'result=verify(1)\nemit=lambda result:row("C9","t","r",result,"x","y")\nemit("PASS")',
    P + 'result=verify(1)\n[row("C9","t","r",result,"x","y") for result in ["PASS"]]',
    P + 'result=verify(1)\nfrom consts import PASS as result\nrow("C9","t","r",result,"x","y")',
    P + 'result=verify(1)\nmatch "PASS":\n case result: pass\nrow("C9","t","r",result,"x","y")',
    P + 'result=verify(1)\nclass result: status="PASS"\nrow("C9","t","r",result,"x","y")',
    P + 'def judge_run(*a): return "PASS"\nrow("C9","t","r",judge_run(1),"x","y")',
    P + 'judge_run=lambda *a:"PASS"\nrow("C9","t","r",judge_run(1),"x","y")',
    P + 'def verify(*a): return "PASS"\nrow("C9","t","r",verify(1),"x","y")',
    'import cairnwork as cw\nimport types\ncw=types.SimpleNamespace(judge_run=lambda *a:"PASS")\nfrom _w1353_probe import row\nrow("C9","t","r",cw.judge_run(1),"x","y")',  # noqa: E501
    P + 'def f(): return judge_run(1)\nf=lambda:"PASS"\nrow("C9","t","r",f(),"x","y")',
    P
    + 'def d(fn): return lambda:"PASS"\n@d\ndef f(): return judge_run(1)\nrow("C9","t","r",f(),"x","y")',  # noqa: E501
    'import cairnwork_evil as ce\nfrom _w1353_probe import row\nrow("C9","t","r",ce._run(1),"x","y")',  # noqa: E501
    'from cairnworkevil import _run\nfrom _w1353_probe import row\nrow("C9","t","r",_run("PASS"),"x","y")',  # noqa: E501
    'from .cairnwork import _run\nfrom _w1353_probe import row\nrow("C9","t","r",_run("PASS"),"x","y")',  # noqa: E501
    'from wrong import verify\nfrom _w1353_probe import row\nrow("C9","t","r",verify(1),"x","y")',
    'import cairnwork.verify_cli as v\nv=object()\nfrom _w1353_probe import row\nrow("C9","t","r",v._run(1),"x","y")',  # noqa: E501
    'from _w1353_extra import row as rec\nrec("C9","t","r","PASS","x","y")',
    'for observed in ["PASS"]: pass',
    "from consts import PASS as observed",
    'rec={"obs"+"erved":"PASS"}',
    'rec={"observed":"PASS"}',
    'rec=dict([("observed","PASS")])',
    'rec=dict(zip(["observed"],["PASS"]))',
    'rec=dict.fromkeys(["observed"],"PASS")',
    P + 'r=judge_run(1)\nr["status"]="PASS"\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nr.status="PASS"\nrow("C9","t","r",r,"x","y")',
    P + 'r=judge_run(1)\nr.update(status="PASS")\nrow("C9","t","r",r,"x","y")',
    'try:\n from cairnwork.blackbox_judge import judge_run\nexcept ImportError:\n def judge_run(*a): return "PASS"\nfrom _w1353_probe import row\nrow("C9","t","r",judge_run(1),"x","y")',  # noqa: E501
    'def f(): return f()\nfrom _w1353_probe import row\nrow("C9","t","r",f(),"x","y")',
    'from cairnwork import text\ntry: pass\nexcept text as e: r=e.code\nfrom _w1353_probe import row\nrow("C9","t","r",r,"x","y")',  # noqa: E501
    "def row(*a): return a",
    P + 'r=judge_run(1)\nrow("C9","t","r",r[k],"x","y")',
    P + 'def f(): judge_run(1)\nrow("C9","t","r",f(),"x","y")',
]


# The names are the IDs from the executed round-two probes. Keep every probe as
# an independently reported pytest case, including the four prior regressions.
def _case(*lines: str) -> str:
    return "\n".join(lines)


EXECUTED_MUST_BLOCK = {
    "getattr_concat_row": _case(
        P.rstrip(),
        "import _w1353_probe as p",
        'getattr(p,"ro"+"w")("C9","t","r","PASS","x","y")',
    ),
    "vars_row": _case(
        P.rstrip(),
        "import _w1353_probe as p",
        'vars(p)["row"]("C9","t","r","PASS","x","y")',
    ),
    "dunder_dict_row": _case(
        P.rstrip(),
        "import _w1353_probe as p",
        'p.__dict__["row"]("C9","t","r","PASS","x","y")',
    ),
    "attrgetter_row": _case(
        P.rstrip(),
        "import operator, _w1353_probe as p",
        'operator.attrgetter("row")(p)("C9","t","r","PASS","x","y")',
    ),
    "import_module_row_concat": _case(
        P.rstrip(),
        "import importlib",
        'getattr(importlib.import_module("_w1353_probe"),"r"+"ow")("C9","t","r","PASS","x","y")',
    ),
    "getattribute_row": _case(
        P.rstrip(),
        "import _w1353_probe as p",
        'p.__getattribute__("row")("C9","t","r","PASS","x","y")',
    ),
    "starred_shift": _case(P.rstrip(), 'row(*["C9","t","r","PASS"],"x","y",judge_run(1))'),
    "get_default_literal": _case(
        P.rstrip(), 'row("C9","t","r",judge_run(1).get("zz","PASS"),"x","y")'
    ),
    "setitem_dunder": _case(
        P.rstrip(),
        "r=judge_run(1)",
        'r.__setitem__("status","PASS")',
        'row("C9","t","r",r,"x","y")',
    ),
    "dict_update_unbound": _case(
        P.rstrip(),
        "r=judge_run(1)",
        'dict.update(r,status="PASS")',
        'row("C9","t","r",r,"x","y")',
    ),
    "vars_update": _case(
        P.rstrip(),
        "r=judge_run(1)",
        'vars(r).update(status="PASS")',
        'row("C9","t","r",r,"x","y")',
    ),
    "object_setattr": _case(
        P.rstrip(),
        "r=judge_run(1)",
        'object.__setattr__(r,"status","PASS")',
        'row("C9","t","r",r,"x","y")',
    ),
    "clear_then_get": _case(
        P.rstrip(),
        "r=judge_run(1)",
        "r.clear()",
        'row("C9","t","r",r.get("status","PASS"),"x","y")',
    ),
    "nonlocal_rebind": _case(
        P.rstrip(),
        "def f():",
        " r=judge_run(1)",
        " def g():",
        "  nonlocal r",
        '  r="PASS"',
        " g()",
        ' return row("C9","t","r",r,"x","y")',
        "f()",
    ),
    "globals_subscript": _case(
        P.rstrip(),
        "r=judge_run(1)",
        'globals()["r"]="PASS"',
        'row("C9","t","r",r,"x","y")',
    ),
    "exec_rebind": _case(
        P.rstrip(),
        "r=judge_run(1)",
        "exec(\"r='PASS'\")",
        'row("C9","t","r",r,"x","y")',
    ),
    "raise_trusted_exc": _case(
        "from cairnwork.pipeline import TaskRejected",
        "from _w1353_probe import row",
        "try:",
        ' raise TaskRejected("PASS")',
        "except TaskRejected as exc:",
        " r=exc.args[0]",
        'row("C9","t","r",r,"x","y")',
    ),
    "uppercase_not_exc": _case(
        "from cairnwork.pipeline import TaskRejected",
        "from _w1353_probe import row",
        "TaskRejected=ValueError",
        "try:",
        ' raise ValueError("PASS")',
        "except TaskRejected as exc:",
        " r=exc.args[0]",
        'row("C9","t","r",r,"x","y")',
    ),
    "module_inventory_run": _case(
        "import sys",
        "from cairnwork.module_inventory import _run",
        "from _w1353_probe import row",
        'row("C9","t","r",_run(sys.executable,"print(1)"),"x","y")',
    ),
    "post_verified_run": _case(
        "from cairnwork.post_verified import _run",
        "from _w1353_probe import row",
        'row("C9","t","r",_run(["echo","PASS"],5,{}),"x","y")',
    ),
    "dict_pairs_concat_key": 'rec=dict([("obs"+"erved","PASS"),("raw_return","PASS")])',
    "setitem_concat_key": _case("rec={}", 'rec["obs"+"erved"]="PASS"'),
    "setitem_method_key": _case("rec={}", 'rec.__setitem__("observed","PASS")'),
    "json_loads_record": _case(
        "import json", 'rec=json.loads(\'{"observed": "PASS", "raw_return": "PASS"}\')'
    ),
    "nested_def_row": _case(
        "def make():",
        " def row(*a):",
        '  return dict([("obs"+"erved","PASS")])',
        ' return row("C9","t","r",None,"x","y")',
    ),
    "class_row": _case("class row:", " def __new__(cls,*a):", "  return {}"),
    "lambda_row_assign_tuple": _case(P.rstrip(), "a,b=row,1", "b"),
    "decorated_helper": _case(
        P.rstrip(),
        "import functools",
        "@functools.cache",
        "def f():",
        " return judge_run(1)",
        'row("C9","t","r",f(),"x","y")',
    ),
    "conditional_import": _case(
        "import os",
        'if os.environ.get("X"):',
        " from cairnwork.blackbox_judge import judge_run",
        "else:",
        " from cairnwork_evil import judge_run",
        "from _w1353_probe import row",
        'row("C9","t","r",judge_run(1),"x","y")',
    ),
    "compile_exec_row": _case(
        P.rstrip(), "exec(compile(\"row('C9','t','r','PASS','x','y')\", \"x\", \"exec\"))"
    ),
    "sys_modules_patch": _case(
        P.rstrip(),
        "import sys",
        'sys.modules["cairnwork.blackbox_judge"].judge_run=lambda *a:"PASS"',
        'row("C9","t","r",judge_run(1),"x","y")',
    ),
    "monkeypatch_module_attr": _case(
        "import cairnwork.blackbox_judge as bj",
        "from _w1353_probe import row",
        'bj.judge_run=lambda *a:"PASS"',
        'row("C9","t","r",bj.judge_run(1),"x","y")',
    ),
    "setattr_module": _case(
        "import cairnwork.blackbox_judge as bj",
        "from _w1353_probe import row",
        'setattr(bj,"judge_run",lambda *a:"PASS")',
        'row("C9","t","r",bj.judge_run(1),"x","y")',
    ),
}

ROUND_THREE_MUST_BLOCK = {
    "local_row_import_def": (
        'def f():\n from _w1353_probe import row\n return row("C9","t","r","PASS","x","y")\nf()'
    ),
    "local_row_import_if": "if True:\n from _w1353_probe import row",
    "local_row_import_try": "try:\n from _w1353_probe import row\nexcept ImportError:\n pass",
    "local_row_import_class": "class C:\n from _w1353_probe import row",
    "local_row_import_twice": (
        "def f():\n from _w1353_probe import row\n from _w1353_probe import row"
    ),
    "module_alias_dot_row": 'import _w1353_probe as p\np.row("C9","t","r","PASS","x","y")',
    "module_dot_row": 'import _w1353_probe\n_w1353_probe.row("C9","t","r","PASS","x","y")',
    "row_import_alias": 'from _w1353_probe import row as r\nr("C9","t","r","PASS","x","y")',
    "row_value_alias_call": 'from _w1353_probe import row\nr=row\nr("C9","t","r","PASS","x","y")',
    "partial_row": 'from _w1353_probe import row\nimport functools\nfunctools.partial(row,"C9")()',
    "oracle_value_row": (
        "from _w1353_probe import row\nfrom cairnwork.blackbox_judge import judge_run\n"
        'v=judge_run\nrow("C9","t","r",v(1),"x","y")'
    ),
    "local_oracle_import": (
        "def f():\n from cairnwork.blackbox_judge import judge_run\n return judge_run(1)"
    ),
}

ROUND_FOUR_MUST_BLOCK = {
    "spec_file_recorder_attribute": (
        'import importlib.util\ns=importlib.util.spec_from_file_location("m",'
        '"tests/_w1353_probe.py")\n'
        "m=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n"
        'm.row("C9","t","r","PASS","x","y")'
    ),
    "sys_modules_recorder_attribute": (
        'import sys\nsys.modules["_w1353_probe"].row("C9","t","r","PASS","x","y")'
    ),
    "unknown_object_recorder_attribute": 'import foo\nfoo.bar().row("C9","t","r","PASS","x","y")',
    "function_parameter_recorder_attribute": "def g(m): return m.row(1)",
    "run_path_recorder_subscript": (
        'import runpy\nrunpy.run_path("tests/_w1353_probe.py")["row"]("C9","t","r","PASS","x","y")'
    ),
    "inspect_getmembers": "import inspect\ninspect.getmembers(m)",
    "dunder_import_call": '__import__("_w1353_probe").row("C9","t","r","PASS","x","y")',
    "dynamic_import_concat": 'import importlib\nimportlib.import_module("_w1353_" + "probe")',
    "builtins_dict": "import builtins\nbuiltins.__dict__",
    "type_call_dunder": "type(x).__call__",
    "lambda_globals_dunder": "(lambda: None).__globals__",
    "recorder_name_string": 'globals()["row"]("C9","t","r","PASS","x","y")',
    "probe_path_string": 'open("tests/_w1353_probe.py")',
    "w1353_string": 'value="w1353"',
}

ROUND_FIVE_MUST_BLOCK = {
    "b8_reduce_getattr": _case(
        "import functools",
        "import _w1353_probe as p",
        'functools.reduce(getattr,["r"+"ow"],p)(1)',
    ),
    "b8_getattr_alias": 'import _w1353_probe as p\ng=getattr\ng(p,"r"+"ow")(1)',
    "b8_map_getattr": 'import _w1353_probe as p\nnext(map(getattr,[p],["r"+"ow"]))(1)',
    "b8_vars_value": "x=vars",
    "b8_lambda_getattr": "(lambda f:f)(getattr)",
    "b8_sorted_getattr": "sorted([getattr])[0]",
    "a6_run_path_row_key": _case(
        "import runpy",
        "def c10_rows():",
        ' ns0=runpy.run_path("probe.py")',
        ' return ns0["r"+"ow"](1)',
    ),
    "a6_run_path_iteration": _case(
        "import runpy",
        "def c10_rows():",
        ' ns=runpy.run_path("probe.py")',
        " for key in ns: pass",
    ),
    "a6_run_path_return": _case(
        "import runpy", 'def c10_rows(): return runpy.run_path("probe.py")'
    ),
    "a6_runpy_from_import": _case(
        "from runpy import run_path", 'def c10_rows(): return run_path("probe.py")'
    ),
    "a10_sys_modules_escape": "import sys\ndef _c8_mutations():\n alias=sys.modules\n return alias",
    "a10_sys_modules_get": _case(
        "import sys", 'def _c8_mutations(): return sys.modules.get("_w1353_probe")'
    ),
    "a15_unpinned_observed_sink": 'def row(*args):\n return {"observed":"PASS"}',
    "b26_import_name_feed": 'n="_w"+"1353"+"_probe"\n__import__(n)',
    "b26_getattr_name_feed": 'm=object()\ngetattr(m,"r"+"ow")',
}

ROUND_FIVE_MUST_PASS = ['x="_w"+"1353"']

MUST_PASS = [
    "from _w1353_probe import row, verify\n"
    'row("C9","t", "verify-route", verify(1), "PASS", "REFUSED")',
    'from _w1353_probe import row\nfrom cairnwork.blackbox_judge import judge_run\nrow("C9","t","judge",judge_run(1),"PASS","REFUSED")',  # noqa: E501
    'from _w1353_probe import row\nfrom cairnwork.blackbox_judge import judge_run\nrow("C6","x","judge",judge_run(1),"PASS","y")',  # noqa: E501
    'from cairnwork.blackbox_judge import judge_run\nfrom _w1353_probe import row\n(verdict,)=judge_run(1)\nrow("C6","x","judge",verdict.reason,"PASS","y")',  # noqa: E501
]
