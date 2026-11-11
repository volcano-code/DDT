"""Minimal JFR exit evidence. Optional diagnosis; never converts an exit to business success.

Only supervisor-created private recordings are parsed. No general JFR recording,
command line, environment dump or raw vendor log is included in the report.
"""
from __future__ import annotations
from contextlib import contextmanager
import json, os, re, stat, tempfile
from pathlib import Path
from .lifecycle import supervise

MECHANISMS = {'non_daemon_threads_finished', 'java_shutdown_requested', 'explicit_system_exit',
              'signal_handler_shutdown', 'shutdown_event_absent', 'unknown'}
MARKER = 'DDT_EXIT_JSON='

@contextmanager
def capture_directory(parent: Path):
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError('Private recording parent required')
    with tempfile.TemporaryDirectory(prefix='.exit-evidence-', dir=parent) as name:
        folder = Path(name); folder.chmod(0o700)
        yield folder / 'exit.jfr'


def validate(value: object, *, run_id: str, pid: int) -> dict:
    if not isinstance(value, dict) or set(value) != {'schema','run_id','pid','mechanism','identity_bound','shutdown_event_present','frames','business_verified'}:
        raise ValueError('Unexpected exit evidence shape')
    if (type(value['schema']) is not int or value['schema'] != 1 or not re.fullmatch('[a-f0-9]{32}', run_id)
        or value['run_id'] != run_id or type(value['pid']) is not int or value['pid'] != pid
        or type(pid) is not int or pid <= 0 or value['identity_bound'] is not True
        or type(value['shutdown_event_present']) is not bool or value['business_verified'] is not False
        or not isinstance(value['mechanism'],str) or value['mechanism'] not in MECHANISMS):
        raise ValueError('Unbound exit evidence')
    if value['shutdown_event_present'] != (value['mechanism'] != 'shutdown_event_absent'):
        raise ValueError('Inconsistent shutdown event state')
    frames = value['frames']
    if not isinstance(frames, list) or len(frames) > 12: raise ValueError('Invalid stack count')
    for f in frames:
        if (not isinstance(f, dict) or set(f) != {'class','method','line'}
            or not isinstance(f['class'], str) or not re.fullmatch(r'[A-Za-z0-9_.$]{1,220}', f['class'])
            or not f['class'].startswith(('com.sd.','port.','java.lang.','jdk.internal.misc.Signal'))
            or not isinstance(f['method'], str) or not re.fullmatch(r'[A-Za-z0-9_$<>]{1,120}', f['method'])
            or type(f['line']) is not int or not -2 <= f['line'] <= 2**31-1):
            raise ValueError('Invalid filtered frame')
    return {**value, 'frames': [dict(f) for f in frames]}


def read_capture(path: Path, *, java: str, overlay: Path, result: dict, env: dict, cwd: Path, prefix: list[str]) -> dict:
    base = {'status':'unavailable','mechanism':'unknown','business_verified':False,'recording_retained':False}
    run_id, pid = result.get('run_id'), result.get('process_pid')
    if (not isinstance(run_id,str) or re.fullmatch('[a-f0-9]{32}',run_id) is None
        or type(pid) is not int or pid <= 0): return {**base,'reason':'process_identity_missing'}
    if not path.exists(): return {**base, 'reason':'recording_missing'}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or not 0 < info.st_size <= 16_000_000:
        return {**base,'reason':'recording_not_private_bounded_regular'}
    argv = [*prefix, java, '-Xmx96m', '-Djava.awt.headless=true', '-cp', str(overlay),
            'port.ExitRecordingReader', str(path), run_id, str(pid)]
    try:
        parsed = supervise(argv, cwd=cwd, env=env, timeout=12, output_limit=65536)
        if (parsed.get('returncode') != 0 or not parsed.get('started')
            or any(parsed.get(k) for k in ('timed_out','cancelled','output_limit_reached','output_truncated'))):
            return {**base,'reason':'recording_reader_failed'}
        lines=[s[len(MARKER):] for s in parsed.get('output','').splitlines() if s.startswith(MARKER)]
        if len(lines)!=1: raise ValueError('Expected one exit evidence record')
        value=validate(json.loads(lines[0]), run_id=run_id, pid=pid)
        return {**value,'status':'verified_recording','recording_retained':False,
                'scope':'jvm_exit_mechanism_not_business_or_root_cause'}
    except (OSError, ValueError): return {**base,'reason':'recording_validation_failed'}
