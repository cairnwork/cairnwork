"""#471 step 1: the socket-walk script's location classes (pure, no sandbox)."""

import importlib.util
from pathlib import Path

_path = Path(__file__).parents[1] / "scripts" / "socket_walk_471.py"
_spec = importlib.util.spec_from_file_location("socket_walk_471", _path)
assert _spec is not None and _spec.loader is not None
walk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(walk)


def test_location_classes():
    cases = {
        "/tmp/x": "private-trees",
        "/run/x": "private-trees",
        "/var/run/x": "private-trees",
        "/var/tmp/x": "var-tmp",
        "/var/lib/x": "var-lib",
        "/home/a/x": "home",
        "/srv/x": "srv",
        "/opt/x": "opt",
        "/usr/lib/x": "usr-and-other-system",
        "/weird/x": "other",
    }
    assert {path: walk.location(path) for path in cases} == cases
