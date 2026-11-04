"""Module inventory probe for a verifier Python older than 3.10 (#790). Never imported.

module_inventory.py reads this file as text and runs it as ``python -I -c <text>`` inside the
verifier's interpreter, so it must stay valid Python 3.6: no walrus, no importlib.metadata, no
f-strings, no positional-only parameters. Listing a name that is not importable only refuses more;
missing one could let a candidate shadow it. So every source is read in full, and anything that
cannot be enumerated (an unreadable directory or metadata file, an import hook not on the list
below) raises, which exits non-zero and leaves every checked path protected. Output: one JSON line
``{"v": 1, "version": [major, minor], "names": [...]}``.
"""

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
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


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


def _scan(directory, names, seen):
    if not _is_dir(directory):
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


def _check_path_machinery():
    for hook in sys.path_hooks:
        if hook is not zipimport.zipimporter and getattr(hook, "__code__", None) is not (
            _FILE_FINDER_HOOK
        ):
            raise SystemExit("unknown path hook: " + repr(hook))
    entries = list(sys.path) + list(sys.path_importer_cache)
    for entry in entries:
        importer = sys.path_importer_cache.get(entry)
        if importer is None and entry in sys.path:
            importer = pkgutil.get_importer(entry)
        if importer is not None and not isinstance(
            importer, (machinery.FileFinder, zipimport.zipimporter)
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
        mapping = getattr(finder, "MAPPING", None)
        if where == ("_distutils_hack", "DistutilsMetaFinder"):
            names.add("distutils")
        elif where == ("_virtualenv", "_Finder"):
            pass  # virtualenv patches distutils on load and adds no name
        elif cls.__module__.startswith("__editable___") and isinstance(mapping, dict):
            names.update(str(key).split(".", 1)[0] for key in mapping)
        else:
            raise SystemExit("unknown meta_path finder: " + cls.__module__ + "." + cls.__name__)
    return names


def main():
    _check_path_machinery()
    names = set(sys.builtin_module_names) | _meta_path_names()
    names.update(name.split(".", 1)[0] for name in list(sys.modules))
    names.update(module[1] for module in pkgutil.iter_modules())
    seen = set()
    for entry in list(sys.path):
        if entry:
            _scan(entry, names, seen)
    names.discard(None)
    names.discard("")
    version = [sys.version_info[0], sys.version_info[1]]
    sys.stdout.write(json.dumps({"v": 1, "version": version, "names": sorted(names)}) + "\n")


if __name__ == "__main__":
    main()
