from __future__ import annotations

import os
import stat

import pytest

from iserv_mcp import server


def test_attachment_root_symlink_is_rejected(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    symlink_root = tmp_path / "attachments"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        server._open_secure_directory(symlink_root)


def test_attachment_write_is_exclusive_and_private(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    directory_fd, _ = server._open_secure_directory(destination)
    try:
        first = server._write_attachment(directory_fd, "letter.pdf", b"first", destination)
        second = server._write_attachment(directory_fd, "letter.pdf", b"second", destination)
    finally:
        os.close(directory_fd)

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert second != first
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(second.stat().st_mode) == 0o600


def test_attachment_write_does_not_follow_filename_symlink(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"original")
    (destination / "report.txt").symlink_to(outside)

    directory_fd, _ = server._open_secure_directory(destination)
    try:
        written = server._write_attachment(directory_fd, "report.txt", b"replacement", destination)
    finally:
        os.close(directory_fd)

    assert outside.read_bytes() == b"original"
    assert written.name != "report.txt"
    assert written.read_bytes() == b"replacement"


def test_missing_o_nofollow_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(server.os, "O_NOFOLLOW", 0)

    with pytest.raises(OSError, match="no-follow"):
        server._open_secure_directory(tmp_path / "attachments")


def test_child_directory_fd_is_closed_when_fchmod_fails(monkeypatch, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_fd, _ = server._open_secure_directory(parent)
    original_open = server.os.open
    original_close = server.os.close
    opened: list[int] = []
    closed: list[int] = []

    def record_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def record_close(fd):
        closed.append(fd)
        return original_close(fd)

    def fail_fchmod(_fd, _mode):
        raise OSError("fchmod failed")

    monkeypatch.setattr(server.os, "open", record_open)
    monkeypatch.setattr(server.os, "close", record_close)
    monkeypatch.setattr(server.os, "fchmod", fail_fchmod)
    try:
        with pytest.raises(OSError, match="fchmod"):
            server._open_secure_child_directory(parent_fd, "child", parent)
        child_fd = original_open("child", os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd)
        original_close(child_fd)
    finally:
        original_close(parent_fd)

    assert opened
    assert opened[-1] in closed


def test_attachment_fd_is_closed_when_fdopen_fails(monkeypatch, tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    destination_fd, _ = server._open_secure_directory(destination)
    opened: list[int] = []
    original_open = server.os.open
    original_close = server.os.close

    def record_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    closed: list[int] = []

    def record_close(fd):
        closed.append(fd)
        return original_close(fd)

    monkeypatch.setattr(server.os, "open", record_open)
    monkeypatch.setattr(server.os, "close", record_close)
    monkeypatch.setattr(server.os, "fdopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fdopen failed")))
    try:
        with pytest.raises(RuntimeError, match="fdopen"):
            server._write_attachment(destination_fd, "letter.pdf", b"body", destination)
    finally:
        original_close(destination_fd)

    assert opened
    assert opened[-1] in closed
    assert not (destination / "letter.pdf").exists()
