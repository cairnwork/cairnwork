"""Turn one collected test (one parametrized case) into draft blackbox cases, or refuse it.

A test becomes checks only if its whole body is checks: literal assignments, ``assert call ==
literal`` (or ``is None/True/False``, ``unittest`` ``assertEqual``/``assertIsNone``), and
``pytest.raises(T)`` around a single call. Any other statement is setup, a side effect or control
flow that a blackbox case cannot repeat, so the test is refused with the line and reason: a
partial conversion would protect less than the test checks. Each case records the kinds it needs
(``function``; ``method``, ``sequence``, ``construct``, ``raises``, ``float`` for later blackbox
versions), so coverage can be computed for any set of supported kinds. Nothing is executed: the
expected values come from the test's own literals, never from running the candidate.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from typing import Any

from .literals import Env, Refused, evaluate
from .resolve import Imports, Project, Target, dotted, resolve_callee

APPROX_DEFAULT = {"rel_tol": 1e-6, "abs_tol": 1e-12}  # pytest.approx defaults
EXACT = {"rel_tol": 0.0, "abs_tol": 0.0}


@dataclass
class Draft:
    """One draft case: what to call, with what, and what the test expects."""

    line: int
    target: dict[str, Any]
    args: list[Any]
    kwargs: dict[str, Any]
    expected: dict[str, Any]
    needs: set[str] = field(default_factory=set)

    def plain(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "target": self.target,
            "args": self.args,
            "kwargs": self.kwargs,
            "expected": self.expected,
            "needs": sorted(self.needs),
        }


@dataclass
class Obj:
    """``name = Cls(...)`` in the test: an instance whose method calls may be checked."""

    target: Target
    init_args: list[Any]
    init_kwargs: dict[str, Any]
    floats: bool
    calls: int = 0


class Body:
    """Walks one test body under one set of parametrize bindings."""

    def __init__(self, imports: Imports, project: Project, env: Env, module_names=()):
        self.imports, self.project, self.env = imports, project, dict(env)
        self.module_names = set(module_names)  # one object shared by every test in the module
        self.objects: dict[str, Obj] = {}
        self.results: dict[str, ast.Call] = {}
        self.used: set[str] = set()
        self.locals: set[str] = set()
        self.passed: dict[str, int] = {}  # name -> calls it is passed to
        self.drafts: list[Draft] = []

    def run(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Draft]:
        body = func.body
        if body and _docstring(body[0]):
            body = body[1:]
        for stmt in body:
            self.statement(stmt)
        if not self.drafts:
            raise Refused("the test has no assertion this tool can read")
        for obj in self.objects.values():
            if obj.calls == 0:
                raise Refused("an object is built but no method of it is checked")
        shared = [n for n, k in self.passed.items() if k > 1 and _mutable(self.env.get(n))]
        if shared:
            raise Refused("a list or dict is passed to several calls (one call may change it)")
        if any(n in self.module_names and _mutable(self.env.get(n)) for n in self.passed):
            raise Refused("a module-level list or dict is passed to a call (other tests share it)")
        if set(self.results) - self.used:
            raise Refused("a call's result is stored but never checked (a side effect)")
        return self.drafts

    def statement(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Pass):
            return
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            return self.assign(stmt)
        if isinstance(stmt, ast.Assert):
            return self.check(stmt.test, stmt.lineno)
        if isinstance(stmt, ast.With):
            return self.raises(stmt)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            return self.unittest_call(stmt.value, stmt.lineno)
        raise Refused(
            f"line {stmt.lineno}: {type(stmt).__name__} is not a check "
            "(setup, side effect or control flow)"
        )

    def assign(self, stmt: ast.Assign) -> None:
        target, value = stmt.targets[0], stmt.value
        if not isinstance(target, ast.Name):
            raise Refused(f"line {stmt.lineno}: assignment to something other than a name")
        name = target.id
        if name in self.locals or name in self.env:  # parametrize and module names included
            raise Refused(f"line {stmt.lineno}: a name is bound twice (state changes in the test)")
        self.locals.add(name)
        if isinstance(value, ast.Call):
            try:
                callee = self.callee(value.func)
            except Refused:
                callee = None  # e.g. obj.method(): refused or used when the result is checked
            if callee is not None and callee.kind == "class":
                args, kwargs, floats = self.arguments(value, "constructor arguments")
                self.objects[name] = Obj(callee, args, kwargs, floats)
            else:
                self.results[name] = value
            return
        if any(isinstance(n, ast.Call) for n in ast.walk(value)):  # _ = [reset()] runs reset
            raise Refused(f"line {stmt.lineno}: an assignment runs a call (setup or side effect)")
        self.env[name] = value  # evaluated (or refused) where it is used

    def root(self, name: str) -> str:
        """The name an alias chain (e = d) ends at: one object under several names."""
        for _ in range(50):
            node = self.env.get(name)
            if not isinstance(node, ast.Name) or node.id == name:
                return name
            name = node.id
        return name

    def reach(self, node: ast.expr) -> set[str]:
        """Every object an argument reaches by name, through aliases and containers (e = [d])."""
        found: set[str] = set()
        todo = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
        while todo and len(found) < 1000:
            name = self.root(todo.pop())
            if name not in found:
                found.add(name)
                value = self.env.get(name)
                if value is not None:
                    todo += [n.id for n in ast.walk(value) if isinstance(n, ast.Name)]
        return found

    def callee(self, func: ast.expr) -> Target:
        head = (dotted(func) or [""])[0]
        if head in self.locals or head in self.env:
            raise Refused("the called name is rebound in the test or bound by parametrize")
        return resolve_callee(func, self.imports, self.project)

    def arguments(self, call: ast.Call, what: str) -> tuple[list[Any], dict[str, Any], bool]:
        if any(isinstance(a, ast.Starred) for a in call.args):
            raise Refused(f"{what} use *args")
        if any(k.arg is None for k in call.keywords):
            raise Refused(f"{what} use **kwargs")
        for node in [*call.args, *(k.value for k in call.keywords)]:
            for name in self.reach(node):
                self.passed[name] = self.passed.get(name, 0) + 1
        args = [evaluate(a, self.env, what=what) for a in call.args]
        kwargs = {k.arg: evaluate(k.value, self.env, what=what) for k in call.keywords if k.arg}
        floats = any(v.floats for v in args) or any(v.floats for v in kwargs.values())
        return [v.value for v in args], {k: v.value for k, v in kwargs.items()}, floats

    def check(self, test: ast.expr, line: int) -> None:
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            raise Refused(f"line {line}: assert is not a single comparison (truthiness or chain)")
        op, left, right = test.ops[0], test.left, test.comparators[0]
        if (
            isinstance(op, ast.Is)
            and isinstance(right, ast.Constant)
            and (right.value is None or type(right.value) is bool)
        ):
            return self.add(left, {"value": right.value, "compare": "exact"}, line, set())
        if not isinstance(op, ast.Eq):
            raise Refused(
                f"line {line}: assert uses {type(op).__name__} (only ==, or is None/True/False)"
            )
        call, literal = (left, right) if self.is_call(left) else (right, left)
        if not self.is_call(call):
            raise Refused(f"line {line}: neither side of == is a call of the project")
        expected, needs = self.expected(literal, line)
        self.add(call, expected, line, needs)

    def is_call(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Call) or (
            isinstance(node, ast.Name) and node.id in self.results
        )

    def expected(self, node: ast.expr, line: int) -> tuple[dict[str, Any], set[str]]:
        if isinstance(node, ast.Call) and (dotted(node.func) or [""])[-1] == "approx":
            if len(node.args) != 1:
                raise Refused(f"line {line}: approx() of something other than one value")
            value = evaluate(node.args[0], self.env, what="approx value")
            tol = dict(APPROX_DEFAULT)
            for k in node.keywords:
                if k.arg not in ("rel", "abs"):
                    raise Refused(f"line {line}: approx({k.arg}=...) is not supported")
                tol[f"{k.arg}_tol"] = evaluate(k.value, self.env, what="approx tolerance").value
            return {"value": value.value, "compare": "isclose", "tolerance": tol}, {"float"}
        value = evaluate(node, self.env, what=f"line {line}: expected value")
        if value.floats:
            return {"value": value.value, "compare": "isclose", "tolerance": EXACT}, {"float"}
        return {"value": value.value, "compare": "exact"}, set()

    def add(self, node: ast.expr, expected: dict[str, Any], line: int, needs: set[str]) -> None:
        if isinstance(node, ast.Name) and node.id in self.results:
            self.used.add(node.id)
            node = self.results[node.id]
        if not isinstance(node, ast.Call):
            raise Refused(f"line {line}: the checked value is not a call of the project")
        draft = self.call(node, line)
        draft.expected = expected
        draft.needs |= needs
        self.drafts.append(draft)

    def call(self, node: ast.Call, line: int) -> Draft:
        """The draft for a call: a module function, a method of a built object, or a constructor."""
        args, kwargs, floats = self.arguments(node, f"line {line}: arguments")
        needs = {"float"} if floats else set()
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id in self.objects)
        ):
            obj = self.objects[func.value.id]
            obj.calls += 1
            needs |= {"method"} | ({"sequence"} if obj.calls > 1 else set())
            needs |= {"float"} if obj.floats else set()
            return Draft(line, self.method_target(obj, func.attr), args, kwargs, {}, needs)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            inner = self.callee(func.value.func)
            if inner.kind != "class":
                raise Refused(f"line {line}: a method of a function's result, not of an object")
            iargs, ikwargs, ifloats = self.arguments(func.value, f"line {line}: constructor")
            obj = Obj(inner, iargs, ikwargs, ifloats, 1)
            needs |= {"method"} | ({"float"} if ifloats else set())
            return Draft(line, self.method_target(obj, func.attr), args, kwargs, {}, needs)
        target = self.callee(func)
        if target.kind == "class":
            target_plain = {"module": target.module, "class": target.name, "method": None}
            return Draft(line, target_plain, args, kwargs, {}, needs | {"construct"})
        plain = {"module": target.module, "function": target.name}
        return Draft(line, plain, args, kwargs, {}, needs | {"function"})

    @staticmethod
    def method_target(obj: Obj, method: str) -> dict[str, Any]:
        return {
            "module": obj.target.module,
            "class": obj.target.name,
            "method": method,
            "init_args": obj.init_args,
            "init_kwargs": obj.init_kwargs,
        }

    def raises(self, stmt: ast.With) -> None:
        line = stmt.lineno
        if len(stmt.items) != 1 or not isinstance(stmt.items[0].context_expr, ast.Call):
            raise Refused(f"line {line}: with-block that is not pytest.raises(T)")
        manager = stmt.items[0].context_expr
        name = (dotted(manager.func) or [""])[-1]
        if name not in ("raises", "assertRaises"):
            raise Refused(f"line {line}: with {name}(...) is not pytest.raises")
        if manager.keywords or len(manager.args) != 1:
            raise Refused(
                f"line {line}: raises() with match=/extra arguments: a type-only "
                "check would be weaker, an exact message is not what match= means"
            )
        if len(stmt.body) != 1:
            raise Refused(f"line {line}: the raises block holds more than one statement")
        inner = stmt.body[0]
        value = inner.value if isinstance(inner, ast.Expr | ast.Assign) else None
        if not isinstance(value, ast.Call):
            raise Refused(f"line {line}: the raises block is not a single call")
        self.add_raise(value, manager.args[0], line)

    def add_raise(self, call: ast.Call, exc: ast.expr, line: int) -> None:
        draft = self.call(call, line)
        draft.expected = {"raises": self.exception(exc, line)}
        draft.needs.add("raises")
        self.drafts.append(draft)

    def exception(self, node: ast.expr, line: int) -> dict[str, str]:
        if isinstance(node, ast.Name) and node.id not in self.imports.names:
            builtin = getattr(builtins, node.id, None)
            if isinstance(builtin, type) and issubclass(builtin, BaseException):
                return {"module": "builtins", "name": node.id, "defined_as": node.id}
        if isinstance(node, ast.Tuple):
            raise Refused(f"line {line}: raises() of several exception types")
        target = self.callee(node)
        if target.kind != "class":
            raise Refused(f"line {line}: the expected exception is not a class")
        return {"module": target.module, "name": target.name, "defined_as": target.defined_in}

    def unittest_call(self, call: ast.Call, line: int) -> None:
        name = (dotted(call.func) or [""])[-1]
        is_self = isinstance(call.func, ast.Attribute) and (dotted(call.func) or [""])[0] == "self"
        if is_self and name == "assertEqual" and len(call.args) == 2 and not call.keywords:
            return self.check(ast.Compare(call.args[0], [ast.Eq()], [call.args[1]]), line)
        if is_self and name == "assertIsNone" and len(call.args) == 1 and not call.keywords:
            return self.check(ast.Compare(call.args[0], [ast.Is()], [ast.Constant(None)]), line)
        if is_self and name == "assertRaises" and len(call.args) >= 2 and not call.keywords:
            fake = ast.Call(call.args[1], list(call.args[2:]), [])
            return self.add_raise(fake, call.args[0], line)
        raise Refused(
            f"line {line}: a bare call ({name or 'expression'}) is a side effect, not a check"
        )


def _mutable(node: ast.expr | None) -> bool:
    return node is not None and not isinstance(node, ast.Constant | ast.UnaryOp)


def _docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def drafts_for(func, bindings: Env, imports: Imports, project: Project, module_env: Env):
    """The draft cases of one collected test, or Refused."""
    module_names = set(module_env) - set(bindings)
    return Body(imports, project, {**module_env, **bindings}, module_names).run(func)
