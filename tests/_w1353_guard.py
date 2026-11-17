"""Allow-list guard for #1353 witnesses (#1398): observations must come from pinned routes."""

from __future__ import annotations

import ast

from _w1353_guard_bind import FUNCS as _FUNCS
from _w1353_guard_bind import collect as _collect
from _w1353_guard_flow import route_predicate as _route_predicate

_ROUTE_PASS_CASES = [
    "from cairnwork.blackbox_judge import judge_run\nr = judge_run(1)\n"
    "row('C9','t','r',r.get('status'),'x','y')",
    "from cairnwork.blackbox_judge import judge_run\nfor r in judge_run(1):\n"
    " row('C9','t','r',r,'x','y')",
    "from cairnwork.blackbox_judge import judge_run\nrow('C9','t','r',judge_run('PASS'),'x','y')",
    "from cairnwork import blackbox_judge\n"
    "(verdict,) = blackbox_judge.judge_run(a,b,c)\n"
    "row('C6','x','judge',verdict.reason,'PASS','y')",
    "from _w1353_probe import verify\nresult = verify(1,2,3,4)\n"
    "status=result.get('status',result.get('error','FAILED'))\n"
    "row('C9','t','r',status,'x','y')",
    "from cairnwork_attest.verify import verify_bundle\n"
    "def check(path):\n return verify_bundle(path)\n"
    "row('C8','n','verify_bundle',check(p),'w','REFUSED')",
    "from cairnwork.pipeline import TaskRejected\n"
    "from cairnwork import verify_cli\ntry:\n raw_result=verify_cli._run(a,b)\n"
    "except TaskRejected as exc:\n raw_result=exc.code\n"
    "row('C7','a','r',raw_result,'x','y')",
]

_P = "from cairnwork.blackbox_judge import judge_run\nfrom _w1353_probe import verify, row\n"
# fmt: off
_MUST_BLOCK = [
    _P + ('result = verify(1)\ndef emit(result):\n'
          '    return row("C9","t","r",result,"x","y")\nemit("PASS")'),
    _P + 'result = verify(1)\nemit = lambda result: row("C9","t","r",result,"x","y")\nemit("PASS")',
    _P + 'result = verify(1)\n[row("C9","t","r",result,"x","y") for result in ["PASS"]]',
    _P + 'result = verify(1)\nfrom consts import PASS as result\nrow("C9","t","r",result,"x","y")',
    _P + ('result = verify(1)\nmatch "PASS":\n    case result:\n'
          '        pass\nrow("C9","t","r",result,"x","y")'),
    _P + 'result = verify(1)\nclass result:\n    status = "PASS"\nrow("C9","t","r",result,"x","y")',
    _P + 'def judge_run(*a):\n    return "PASS"\nrow("C9","t","r",judge_run(1),"x","y")',
    _P + 'judge_run = lambda *a: "PASS"\nrow("C9","t","r",judge_run(1),"x","y")',
    _P + 'def verify(*a):\n    return "PASS"\nrow("C9","t","r",verify(1),"x","y")',
    "import cairnwork as cw\nimport types\n"
    'cw = types.SimpleNamespace(judge_run=lambda *a: "PASS")\n'
    'row("C9","t","r",cw.judge_run(1),"x","y")',
    _P + 'def f():\n    return judge_run(1)\nf = lambda: "PASS"\nrow("C9","t","r",f(),"x","y")',
    _P + ('def d(fn):\n    return lambda: "PASS"\n@d\ndef f():\n'
          '    return judge_run(1)\nrow("C9","t","r",f(),"x","y")'),
    'import cairnwork_evil as ce\nrow("C9","t","r",ce._run(1),"x","y")',
    'from cairnworkevil import _run\nrow("C9","t","r",_run("PASS"),"x","y")',
    'from .cairnwork import _run\nrow("C9","t","r",_run("PASS"),"x","y")',
    'from _w1353_extra import row as rec\nrec("C9","t","r","PASS","x","y")',
    'for observed in ["PASS"]:\n    pass',
    "from consts import PASS as observed",
    *[f"rec = {s}" for s in ('{"obs"+"erved": "PASS"}', 'dict([("observed", "PASS")])',
                              'dict(zip(["observed"],["PASS"]))')],
    'rec = dict.fromkeys(["observed"], "PASS")',
    *[_P + f'r = judge_run(1)\n{m}\nrow("C9","t","r",r,"x","y")' for m in
      ('r["status"] = "PASS"', 'r.status = "PASS"', 'r.update(status="PASS")')],
    "try:\n    from cairnwork.blackbox_judge import judge_run\nexcept ImportError:\n"
    '    def judge_run(*a):\n        return "PASS"\n'
    'row("C9","t","r",judge_run(1),"x","y")',
    'def f():\n    return f()\nrow("C9","t","r",f(),"x","y")',
    "from cairnwork import text\ntry:\n    pass\nexcept text as e:\n"
    '    r = e.code\nrow("C9","t","r",r,"x","y")',
    "def row(*a): return a",
    _P + 'r = judge_run(1)\nrow("C9","t","r",r[k],"x","y")',
    _P + 'def f():\n    judge_run(1)\nrow("C9","t","r",f(),"x","y")',
]
# fmt: on


def _literal_observations(source, route_predicate=None, *, probe=False):
    """Line numbers of literal or smuggled observations; ``probe`` marks the probe source."""
    tree = ast.parse(source)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    facts = _collect(tree, parents)
    is_route = route_predicate or _route_predicate(facts, probe)
    flagged = _find(tree, parents, facts, probe, is_route)
    if not probe:
        flagged |= {n.lineno for n in tree.body if isinstance(n, _FUNCS) and n.name == "row"}
    return sorted(flagged)


def _in_probe_row(node, parents, probe):
    while node in parents and not isinstance(node, _FUNCS):
        node = parents[node]
    return bool(
        probe
        and isinstance(node, ast.FunctionDef)
        and node.name == "row"
        and isinstance(parents.get(node), ast.Module)
    )


def _route_assignment(node, parents, is_route):
    parent = parents.get(node)
    while isinstance(parent, (ast.Tuple, ast.List, ast.Starred)):
        parent = parents.get(parent)
    return isinstance(parent, (ast.Assign, ast.AnnAssign)) and is_route(parent.value)


def _find(tree, parents, facts, probe, is_route):
    flagged = set()
    for n in ast.walk(tree):
        binds_observed = (
            isinstance(n, ast.arg)
            and n.arg == "observed"
            or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.name == "observed"
            or isinstance(n, ast.ExceptHandler)
            and n.name == "observed"
            or isinstance(n, ast.MatchAs)
            and n.name == "observed"
            or isinstance(n, ast.MatchStar)
            and n.name == "observed"
            or isinstance(n, ast.MatchMapping)
            and n.rest == "observed"
            or isinstance(n, (ast.Import, ast.ImportFrom))
            and any(
                (
                    a.asname or a.name.split(".")[0]
                    if isinstance(n, ast.Import)
                    else a.asname or a.name
                )
                == "observed"
                for a in n.names
            )
        )
        if binds_observed and not _in_probe_row(n, parents, probe):
            flagged.add(getattr(n, "lineno", 0))
        if isinstance(n, ast.Call) and _is_row_call(n, facts["row"]):
            raw = (
                n.args[3]
                if len(n.args) > 3
                else next((k.value for k in n.keywords if k.arg == "raw"), None)
            )
            if raw is None or not is_route(raw):
                flagged.add(n.lineno)
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if (
                any("observed" in _names(t) for t in targets)
                and not _in_probe_row(n, parents, probe)
                and not is_route(n.value)
            ):
                flagged.add(n.lineno)
        if (
            isinstance(n, ast.Name)
            and isinstance(n.ctx, (ast.Store, ast.Del))
            and n.id == "observed"
        ):
            parent = parents.get(n)
            if (
                not _in_probe_row(n, parents, probe)
                and not isinstance(parent, (ast.Assign, ast.AnnAssign))
                and not _route_assignment(n, parents, is_route)
            ):
                flagged.add(n.lineno)
        if isinstance(n, ast.Dict):
            for key, value in zip(n.keys, n.values, strict=True):
                if not isinstance(key, ast.Constant) and not _in_probe_row(n, parents, probe):
                    flagged.add(n.lineno)
                if isinstance(key, ast.Constant) and key.value == "observed":
                    if not (
                        isinstance(value, ast.Name)
                        and value.id == "observed"
                        and _in_probe_row(n, parents, probe)
                    ):
                        flagged.add(n.lineno)
        flagged |= _smuggled(n, parents, facts["row"], is_route)
    return flagged


def _constant_observed(node):
    return any(isinstance(n, ast.Constant) and n.value == "observed" for n in ast.walk(node))


def _smuggled(n, parents, rows, is_route):
    if isinstance(n, ast.Call):
        fn = n.func
        if (
            isinstance(fn, ast.Name)
            and fn.id in {"dict", "zip"}
            and any(_constant_observed(a) for a in n.args)
            or isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "dict"
            and fn.attr == "fromkeys"
            and any(_constant_observed(a) for a in n.args)
        ):
            return {n.lineno}
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr in {"update", "setdefault"}
            and is_route(fn.value)
        ):
            return {n.lineno}
        if isinstance(fn, ast.Name) and fn.id == "setattr" and n.args and is_route(n.args[0]):
            return {n.lineno}
        if any(k.arg == "observed" for k in n.keywords):
            return {n.lineno}
        if isinstance(fn, ast.Name) and fn.id in {"getattr", "hasattr", "setattr"}:
            key = n.args[1] if len(n.args) > 1 else None
            if isinstance(key, ast.Constant) and key.value in {"row", "observed"}:
                return {n.lineno}
    if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, ast.Store):
        base = n.value
        if is_route(base):
            return {n.lineno}
        if (
            isinstance(n, ast.Subscript)
            and isinstance(n.slice, ast.Constant)
            and n.slice.value == "observed"
            and is_route(base)
        ):
            return {n.lineno}
    aliased = (isinstance(n, ast.Name) and n.id in rows) or (
        isinstance(n, ast.Attribute) and n.attr == "row"
    )
    if aliased and isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Load):
        parent = parents.get(n)
        if not (isinstance(parent, ast.Call) and parent.func is n):
            return {n.lineno}
    return set()


def _names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_names(item) for item in target.elts))
    if isinstance(target, ast.Starred):
        return _names(target.value)
    return set()


def _is_row_call(call, rows):
    fn = call.func
    return (isinstance(fn, ast.Name) and fn.id in rows) or (
        isinstance(fn, ast.Attribute) and fn.attr == "row"
    )
