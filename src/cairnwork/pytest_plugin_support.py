"""Select table plugins used by the base configuration and its tests."""

from __future__ import annotations

import json
import re
import subprocess

from cairnwork.gitcmd import GitCommandError, run_git

_FIXTURES = {
    "pytest_mock": {"mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker"},
    "pytest_django.plugin": {
        "transactional_db",
        "db",
        "client",
        "django_db_setup",
        "django_db_blocker",
        "admin_client",
        "admin_user",
        "rf",
        "async_rf",
        "live_server",
        "django_user_model",
        "django_assert_num_queries",
        "mailoutbox",
    },
    "anyio.pytest_plugin": {"anyio_backend"},
    "pytest_asyncio.plugin": {"event_loop"},
}


_MARKERS = {
    "django_db": "pytest_django.plugin",
    "anyio": "anyio.pytest_plugin",
    "asyncio": "pytest_asyncio.plugin",
}
_MARKER = re.compile(r"pytest\.mark\.(" + "|".join(_MARKERS) + r")\b")


def base_plugins(settings, repo, base, targets, table):
    values = dict(settings.values)
    fixtures = set(str(values.get("usefixtures", "")).split())
    marked: set[str] = set()
    if base:
        names = {name for group in _FIXTURES.values() for name in group}
        pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\b")
        for target in targets:
            path = target.split("::", 1)[0]
            try:
                text = run_git(repo, "show", f"{base}:{path}")
            except GitCommandError:  # a file the base lacks (the task's own tests): cannot tell
                return tuple(module for _, module, _ in table)
            fixtures.update(pattern.findall(text))
            marked.update(_MARKERS[m] for m in _MARKER.findall(text))
    return tuple(
        module
        for _, module, keys in table
        if keys.intersection(values)
        or module in marked
        or fixtures.intersection(_FIXTURES.get(module, set()))
    )


def installed_plugins(python, table):
    source = (
        "import importlib.util as u, json, sys;\n"
        "def find(n):\n"
        "    parent, _, _ = n.rpartition('.')\n"
        "    if not parent: return u.find_spec(n) is not None\n"
        "    spec = u.find_spec(parent)\n"
        "    return (spec is not None and spec.submodule_search_locations is not None\n"
        "            and u.find_spec(n, spec.submodule_search_locations) is not None)\n"
        "print(json.dumps([find(n) for n in sys.argv[1:]]))"
    )
    done = subprocess.run(  # noqa: S603 - trusted interpreter path chosen by Cairnwork
        [python, "-I", "-c", source, *(module for _, module, _ in table)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    found = json.loads(done.stdout)
    return tuple(module for (_, module, _), ok in zip(table, found, strict=True) if ok)


def plugins_for_run(batch, spec, python, table, installed):
    """Installed table plugins that the base's config, fixtures or targets actually need."""
    required = base_plugins(
        batch.settings, batch.ctx.repo, batch.ctx.base_commit, spec.targets, table
    )
    return tuple(m for m in installed(python) if m in required)
