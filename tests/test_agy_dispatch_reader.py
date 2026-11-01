"""StatusReader keeps draining a dispatcher's stderr through isolated read errors."""


def test_isolated_read_failures_never_stop_the_draining():
    import io

    from cairnwork.workers.agy_dispatch import StatusReader

    class EveryOther(io.BytesIO):
        calls = 0

        def readline(self, size: int | None = -1):
            self.calls += 1
            if self.calls % 2:
                raise OSError("boom")
            return super().readline(size)

    lines = b"noise\n" * 5 + b'NEXUS_AGY_DISPATCH {"status": "pool_exhausted"}\n'
    reader = StatusReader(EveryOther(lines))
    reader.run()  # five isolated failures, never three in a row
    assert reader.status() == {"status": "pool_exhausted"}


def test_a_status_line_too_deep_to_parse_reads_as_no_status(monkeypatch):
    """#537 B: json.loads raises RecursionError for deep nesting (Python 3.11-3.13 within the
    line cap); like any malformed line it reads as no status instead of raising."""
    from cairnwork.workers import agy_dispatch

    deep = agy_dispatch.STATUS_PREFIX + "[" * 32000 + "]" * 32000
    assert agy_dispatch.read_status(deep) == {}

    def too_deep(_text):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(agy_dispatch.json, "loads", too_deep)  # every version, whatever its depth
    assert agy_dispatch.read_status(agy_dispatch.STATUS_PREFIX + '{"status": "ok"}') == {}
