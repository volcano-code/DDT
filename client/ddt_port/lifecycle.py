"""Single-workspace lifecycle and bounded process supervision, Linux only.

These controls protect against accidental concurrent use; they are not a sandbox
against the local administrator or a hostile JVM.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import threading
import time

from .installer import checked_root, managed_path

_local = threading.local()


class WorkspaceBusy(ValueError):
    """Another process or thread owns the workspace."""


@contextmanager
def workspace_lock(root: Path):
    root = checked_root(root)
    key = str(root.resolve())
    owned = getattr(_local, 'owned', {})
    if key in owned:
        yield
        return
    path = managed_path(root, '.operation.lock')
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Workspace lock is not a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as ex:
            raise WorkspaceBusy('Workspace busy: close its running client or wait for its operation to finish') from ex
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({'pid': os.getpid(), 'purpose': 'port-operation'}).encode())
        owned[key] = fd
        _local.owned = owned
        try:
            yield
        finally:
            owned.pop(key, None)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    # Do NOT unlink a flock file: another process may already have opened its inode.


def locked(function):
    @wraps(function)
    def wrapper(root: Path, *args, **kwargs):
        with workspace_lock(root):
            from .toolchain import execution
            with execution(root):
                return function(root, *args, **kwargs)
    return wrapper


def validate_timeout(value: float, *, allow_unbounded: bool = False) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_unbounded):
        raise ValueError('Timeout must be finite and positive; only an explicit client launch allows 0')
    if value > 3600:
        raise ValueError('Diagnostic timeout exceeds one hour')
    return value


def _stop_group(process: subprocess.Popen, grace: float = 1.5) -> None:
    """Stop descendants as well as the leader; do not leave a WebKit helper behind."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    until = time.monotonic() + grace
    while time.monotonic() < until:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.025)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=5)


def supervise(command: list[str], *, cwd: Path, env: dict[str, str],
              timeout: float, cancel: threading.Event | None = None,
              output_limit: int = 1_000_000) -> dict:
    """Run argv without a shell. Own and clean up the whole process group."""
    validate_timeout(timeout, allow_unbounded=True)
    if not command or any(not isinstance(x, str) or '\0' in x for x in command):
        raise ValueError('Invalid process argv')
    if output_limit < 1024:
        raise ValueError('Output limit too small')
    if cancel is not None and cancel.is_set():
        return {'returncode': None, 'cancelled': True, 'timed_out': False,
                'output_limit_reached': False, 'output': '', 'output_truncated': False,
                'elapsed_seconds': 0.0, 'started': False}
    start = time.monotonic()
    timed_out = cancelled = exceeded = False
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                timed_out = timeout > 0 and time.monotonic() - start >= timeout
                cancelled = cancel is not None and cancel.is_set()
                exceeded = os.fstat(log.fileno()).st_size > output_limit
                if timed_out or cancelled or exceeded:
                    break
                time.sleep(0.03)
        finally:
            # Applies on KeyboardInterrupt, leader exit, cancellation and timeout.
            _stop_group(process)
        log.seek(0, os.SEEK_END)
        size = log.tell()
        exceeded = exceeded or size > output_limit
        log.seek(max(0, size - 32768))
        output = log.read(32768).decode('utf-8', 'replace')
    return {'returncode': process.returncode, 'cancelled': cancelled,
            'timed_out': timed_out, 'output_limit_reached': exceeded,
            'elapsed_seconds': round(time.monotonic() - start, 3), 'process_pid': process.pid, 'output': output,
            'output_truncated': size > 32768, 'started': True}
