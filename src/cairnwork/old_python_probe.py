"""Module inventory probe for a verifier Python older than 3.10 (#790). Never imported.

module_inventory.py reads this file as text and runs it as ``python -I -c <text>`` inside the
verifier's interpreter, so it must stay valid Python 3.6: no walrus, no importlib.metadata, no
f-strings, no positional-only parameters. Listing a name that is not importable only refuses more;
missing one could let a candidate shadow it. So every source is read in full, and anything that
cannot be enumerated (an unreadable directory or metadata file, an import hook not on the list
below, a standard hook or finder whose loaders or suffixes are not CPython's startup set, #841)
raises, which exits non-zero and leaves every checked path protected. Output: one JSON line
``{"v": 1, "version": [major, minor], "names": [...]}``.

On Python 3.10+ module_inventory runs it too, with ``PART = "3.10+"`` set first (#811): the same
hook checks, then only what the 3.10+ listing misses (meta_path finder names and the
identifier-named directories of every sys.path directory, importable as namespace packages).
"""

import _imp
import errno
import importlib.machinery as machinery
import json
import os
import pkgutil
import stat
import sys
import zipimport

_FILE_FINDER_HOOK = machinery.FileFinder.path_hook((machinery.SourceFileLoader, [".py"])).__code__


def _top(entry):
    entry = entry.replace("\\", "/").split("/", 1)[0]
    if entry in ("", ".", "..", "__pycache__"):
        return None
    if entry.lower().endswith((".dist-info", ".egg-info", ".data")):
        return None
    return entry.split(".", 1)[0] or None


def _lines(path):
    """A file's lines; [] only when it does not exist, any other error fails the probe."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return []
        raise


def _metadata_names(meta):
    found = set()
    for name in ("top_level.txt", "namespace_packages.txt"):
        found.update(_top(line.strip()) for line in _lines(os.path.join(meta, name)))
    for name in ("RECORD", "installed-files.txt", "SOURCES.txt"):
        for line in _lines(os.path.join(meta, name)):
            path = line.split(",", 1)[0].strip()
            while path.startswith("../"):  # installed-files.txt is relative to the egg-info
                path = path[3:]
            found.add(_top(path))
    return found


def _is_dir(path):
    """Like os.path.isdir, but a directory the probe may not look at fails instead of counting
    as absent: the verifier's account might see what this one cannot."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise


def _exists(path):
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise
    return True


def _scan(directory, names, seen):
    if not _is_dir(directory):
        if _exists(directory):  # a zip or .egg file: pkgutil lists it through zipimport
            names.update(module[1] for module in pkgutil.iter_modules([directory]))
        return
    real = os.path.realpath(directory)
    if real in seen:
        return
    seen.add(real)
    names.update(module[1] for module in pkgutil.iter_modules([real]))
    for entry in os.listdir(real):
        low, full = entry.lower(), os.path.join(real, entry)
        if entry.isidentifier() and _is_dir(full):
            names.add(entry)  # a namespace portion: pkgutil does not list it
        if low.endswith((".dist-info", ".egg-info")):
            if _is_dir(full):
                names.update(_metadata_names(full))
            names.add(_top(entry.split("-", 1)[0]))
        elif low.endswith(".egg-link"):
            target = (_lines(full) or [""])[0].strip()
            if target:
                _scan(os.path.join(real, target), names, seen)
        elif low.endswith(".pth"):
            for line in _lines(full):
                line = line.strip()
                if line and not line.startswith(("#", "import ", "import\t")):
                    _scan(os.path.join(real, line), names, seen)


def _is_editable(cls, name):
    """A class setuptools generated into an ``__editable___*_finder`` module (PEP 660)."""
    return (
        isinstance(cls, type)
        and cls.__name__ == name
        and cls.__module__.startswith("__editable___")
    )


def _editable_names(cls):
    """The names setuptools keeps in the module globals MAPPING and NAMESPACES, not on the class."""
    module = sys.modules.get(cls.__module__)
    mapping = getattr(module, "MAPPING", None)
    spaces = getattr(module, "NAMESPACES", None)
    if not isinstance(mapping, dict) or not isinstance(spaces, dict):
        raise SystemExit("unreadable editable finder: " + cls.__module__)
    return {str(key).split(".", 1)[0] for key in list(mapping) + list(spaces)}


def _default_loaders():
    """The (loader, suffixes) pairs CPython gives FileFinder at startup. An environment that adds
    a suffix (to a hook, a finder, or the shared machinery lists) serves files no listing reads."""
    source = [".py", ".pyw"] if sys.platform == "win32" else [".py"]
    return [
        (machinery.ExtensionFileLoader, _imp.extension_suffixes()),
        (machinery.SourceFileLoader, source),
        (machinery.SourcelessFileLoader, [".pyc"]),
    ]


def _default_hook(hook):
    """The standard FileFinder path hook with the startup loaders (any other details: #841)."""
    if getattr(hook, "__code__", None) is not _FILE_FINDER_HOOK:
        return False
    values = [c.cell_contents for c in hook.__closure__]
    cells = dict(zip(hook.__code__.co_freevars, values))  # noqa: B905 - no strict= on 3.6
    details = [(loader, list(suffixes)) for loader, suffixes in cells["loader_details"]]
    return cells["cls"] is machinery.FileFinder and details == _default_loaders()


def _default_finder(importer):
    """A FileFinder (not a subclass) whose loaders are the startup ones (#841)."""
    want = [(suffix, loader) for loader, suffixes in _default_loaders() for suffix in suffixes]
    return (
        type(importer) is machinery.FileFinder and list(getattr(importer, "_loaders", ())) == want
    )


def _check_path_machinery():
    for hook in sys.path_hooks:
        if (
            hook is not zipimport.zipimporter
            and not _default_hook(hook)
            and not (
                getattr(hook, "__name__", "") == "_path_hook"
                and _is_editable(getattr(hook, "__self__", None), "_EditableNamespaceFinder")
            )
        ):
            raise SystemExit("unknown path hook: " + repr(hook))
    entries = list(sys.path) + list(sys.path_importer_cache)
    for entry in entries:
        importer = sys.path_importer_cache.get(entry)
        if importer is None and entry in sys.path:
            importer = pkgutil.get_importer(entry)
        if (
            importer is not None
            and type(importer) is not zipimport.zipimporter
            and not _default_finder(importer)
            and not _is_editable(importer, "_EditableNamespaceFinder")
        ):
            raise SystemExit("unknown path importer for " + repr(entry))


def _meta_path_names():
    names = set()
    standard = (machinery.PathFinder, machinery.BuiltinImporter, machinery.FrozenImporter)
    for finder in sys.meta_path:
        if any(finder is known for known in standard):
            continue
        cls = finder if isinstance(finder, type) else type(finder)
        where = (cls.__module__, cls.__name__)
        if where == ("_distutils_hack", "DistutilsMetaFinder"):
            names.add("distutils")
        elif where == ("_virtualenv", "_Finder"):
            pass  # virtualenv patches distutils on load and adds no name
        elif _is_editable(cls, "_EditableFinder"):
            names.update(_editable_names(cls))
        else:
            raise SystemExit("unknown meta_path finder: " + cls.__module__ + "." + cls.__name__)
    return names


def _namespace_dirs():
    """Identifier-named directories of every sys.path directory: the 3.10+ part (#811)."""
    names, seen = set(), set()
    for entry in list(sys.path):
        if not entry or not _is_dir(entry) or os.path.realpath(entry) in seen:
            continue
        real = os.path.realpath(entry)
        seen.add(real)
        names.update(n for n in os.listdir(real) if n.isidentifier() and _is_dir(real + "/" + n))
    return names


def main(part=False):
    _check_path_machinery()
    names = _meta_path_names()
    if part:
        names.update(_namespace_dirs())
    else:
        names.update(_rest())
    version = [sys.version_info[0], sys.version_info[1]]
    sys.stdout.write(json.dumps({"v": 1, "version": version, "names": sorted(names)}) + "\n")


def _rest():
    names = set(sys.builtin_module_names)
    names.update(name.split(".", 1)[0] for name in list(sys.modules))
    names.update(module[1] for module in pkgutil.iter_modules())
    seen = set()
    for entry in list(sys.path):
        if entry:
            _scan(entry, names, seen)
    names.discard(None)
    names.discard("")
    return names


if __name__ == "__main__":
    main(globals().get("PART") == "3.10+")
