"""The agy worker's pipes: feed the prompt, close them, also when cleanup fails (#1345)."""

from __future__ import annotations

from typing import NoReturn


def close_streams(process, log) -> None:
    """Attempt every stream; re-raise one error afterwards, an interrupt before an ordinary one."""
    first_error = None
    for stream in (process.stdin, process.stdout, process.stderr, log):
        if stream is not None:
            try:
                stream.close()
            except BaseException as exc:  # noqa: BLE001 - attempt every stream close
                if first_error is None or (
                    not isinstance(exc, Exception) and isinstance(first_error, Exception)
                ):  # an interrupt (KeyboardInterrupt, SystemExit) outranks an ordinary error
                    first_error = exc
    if first_error is not None:
        raise first_error


def fail_cleanup(primary: BaseException, failure: BaseException, process, log, threads) -> NoReturn:
    """``failure`` is the cleanup (``_stop``) error; ``primary`` the error that should leave
    (the one in flight, else ``failure`` itself). An interrupt always leaves as it is. Streams
    are closed only when no thread still reads them (closing under a reader would hang)."""
    if not isinstance(failure, Exception):
        raise failure
    if primary is not failure:
        primary.add_note(f"process cleanup failed: {failure!r}")
    if not any(t is not None and t.is_alive() for t in threads):
        try:
            close_streams(process, log)
        except BaseException as close_error:  # noqa: BLE001 - keep the primary error
            if not isinstance(close_error, Exception):
                raise
            primary.add_note(f"stream cleanup failed: {close_error!r}")
    if primary is failure:
        raise failure
    raise primary from failure


def feed(process, prompt: str) -> None:
    try:
        process.stdin.write(prompt.encode())
    except OSError:
        pass  # the dispatcher exited without reading it; its exit code says what happened
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
