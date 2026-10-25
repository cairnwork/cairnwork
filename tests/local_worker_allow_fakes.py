"""A fake checkout with harness-laid files for the start allow-list tests (#261)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from local_worker_fakes import World, canary_checks

from experimental.local_worker.canary import SCHEMA as CANARY_SCHEMA

HARNESS = {"data/x.dat": "one\ntwo\n", "data/sub/y.dat": "y\n", "gen/fixture.json": "{}\n"}


class AllowWorld(World):
    """World plus what a harness lays down: ``ignored`` directories (status lists only "dir/"),
    empty directories (``x d``), FIFOs (``x o``) and symlinks (digest mode 120000), and the
    wrapper's ``digest`` query (mode and blob id of every path that differs from HEAD)."""

    def __init__(self, extra: dict[str, str] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.files.update(HARNESS if extra is None else extra)
        self.ignored: tuple[str, ...] = ("data/",)
        self.empty_dirs: set[str] = set()
        self.fifos: set[str] = set()
        self.symlinks: set[str] = set()

    def handle(self, action: str, args: list[str], stdin: bytes) -> tuple[int | None, bytes]:
        if action == "digest":
            return 0, self.digest()
        return super().handle(action, args, stdin)

    def status(self) -> bytes:
        recs = [f"# branch.oid {self.head}", "# branch.head main"]
        dirs: set[str] = set()
        for p in self.changed():
            hidden = next((d for d in self.ignored if p.startswith(d)), None)
            if p in self.base:
                recs.append(f"1 .M N... 100644 100644 100644 {'b' * 40} {'b' * 40} {p}")
            elif hidden is not None:
                dirs.add(hidden)  # git lists an ignored directory once, as "dir/"
            elif p.endswith(".log"):
                recs.append(f"! {p}")
            else:
                recs.append(f"? {p}")
        recs += [f"! {d}" for d in sorted(dirs)]
        recs += [f"x d ./{d}" for d in sorted(self.empty_dirs)]
        recs += [f"x o ./{f}" for f in sorted(self.fifos)]
        return ("\0".join(recs) + "\0").encode()

    def digest(self) -> bytes:
        out = []
        for p in self.changed():
            if p not in self.files:
                out.append(f":100644 000000 {'b' * 40} {'0' * 40} D\0{p}\0")
                continue
            mode = "120000" if p in self.symlinks else "100644"
            blob = hashlib.sha1(self.files[p].encode()).hexdigest()  # noqa: S324 - a fake id
            old, status = ("100644", "M") if p in self.base else ("000000", "A")
            out.append(f":{old} {mode} {'b' * 40 if p in self.base else '0' * 40} {blob} ")
            out[-1] += f"{status}\0{p}\0"
        return "".join(out).encode()


def canary_report(tmp_path, wrapper_bytes: bytes):
    wrapper = tmp_path / "local-worker-tool"
    wrapper.write_bytes(wrapper_bytes)
    report = {
        "schema": CANARY_SCHEMA,
        "checks": canary_checks(),
        "overall": "PASS",
        "mode": "worker",
        "wrapper": str(wrapper),
        "wrapper_sha256": hashlib.sha256(wrapper_bytes).hexdigest(),
        "host": "mini",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    data = json.dumps(report).encode()
    path = tmp_path / "out" / "canary.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    Path(str(path) + ".sha256").write_text(hashlib.sha256(data).hexdigest() + "  x\n")
    return path, wrapper, tmp_path / "out"
