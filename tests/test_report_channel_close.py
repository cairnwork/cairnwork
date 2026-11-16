"""Report channel cleanup regression (#1364)."""

import errno
import os

import pytest

from cairnwork import report_channel


def test_close_closes_report_when_nonce_fd_close_fails(tmp_path, monkeypatch):
    channel = report_channel.open_channel(tmp_path)
    nonce_fd = channel._nonce_read
    real_close = os.close
    raised = False

    def fail_once(fd):
        nonlocal raised
        if fd == nonce_fd and not raised:
            raised = True
            raise OSError(errno.EBADF, "x")
        real_close(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(report_channel.os, "close", fail_once)
            with pytest.raises(OSError):
                channel.close()
        assert channel._report.closed
        assert channel._nonce_read == -1
    finally:
        if channel._nonce_read >= 0:
            real_close(channel._nonce_read)
        else:
            try:
                real_close(nonce_fd)
            except OSError:
                pass
        channel._report.close()
