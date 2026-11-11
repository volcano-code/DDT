"""Layered local acceptance. No vendor requests or invented success states."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
import zipfile

from . import __version__, installer, patches, runtime, abi
from .constants import SWT_URL, SWT_SHA512, SWT_CHECKSUM_URL
from .lifecycle import workspace_lock, validate_timeout


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_json(path: Path, value: dict) -> None:
    """Write a fresh evidence file; never replace an existing report or symlink."""
    path = path.absolute()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _check(code: str, ok: bool, label: str, action: str = '') -> dict:
    return {'code': code, 'status': 'passed' if ok else 'blocked',
            'label': label, 'action': '' if ok else action}


def _abi_required(root: Path) -> None:
    if not abi.check(root)['passed']:raise ValueError('Incompatible replacement members')

def _diagnose(root: Path | None) -> dict:
    """Return all relevant failures, without returning HOME, credentials or client logs."""
    base = runtime.doctor(None)
    checks = [
        _check('PLATFORM', base['machine'] == 'x86_64' and os.name == 'posix' and
               __import__('platform').system() == 'Linux', 'Linux x86_64', '仅在 Linux x86_64 测试环境运行。'),
        _check('JAVA', base['java']['available'] and base['java']['major'] in (17, 21),
               'Java 17 / 21', '安装 Linux JDK 17 或 21，不能使用包内 Windows Java。'),
        {'code':'JAVAC','status':'not_required','label':'正常运行使用预编译兼容层','action':'开发重建时单独验证同套 JDK 的 javac 与 --release 17。'},
        _check('GTK3', bool(base['libraries']['gtk-3']), 'GTK3 运行库', '通过当前发行版包管理器安装 GTK3。'),
        _check('WEBKIT', bool(base['libraries']['webkit2gtk-4.1'] or base['libraries']['webkit2gtk-4.0']),
               'WebKitGTK 运行库', 'Debian 13：由管理员安装 libwebkit2gtk-4.1-0；不要混装其他发行版软件包。'),
        _check('DISPLAY', base['display_configured'], 'X11 / XWayland 显示会话', '从普通桌面用户的终端运行；探针可使用 Xvfb。'),
        _check('NON_ROOT', os.geteuid() != 0, '普通桌面用户', '原客户端图形测试不能以 root 运行；不要关闭 WebKit 沙箱。'),
    ]
    ns_ok = False
    try:
        from .toolchain import selected_namespace, inspect_namespace
        inspect_namespace(Path(selected_namespace()));ns_ok=True
    except (ValueError,OSError,subprocess.SubprocessError):pass
    checks.append(_check('OFFLINE_ISOLATION', ns_ok, '离线网络隔离',
                         '当前环境无法创建用户/网络命名空间；不能把未隔离运行标成离线测试。'))
    operations = [
        ('ROOT', '已准备的客户端目录', installer.checked_root, '选择已准备目录，或选择原安装包执行“准备 / 重建”。'),
        ('PAYLOAD', '原载荷完整性', installer.verify_payload, '重新从固定原安装包提取到新目录；不要直接修改原文件。'),
        ('PATCH', '局部平台补丁', patches.verify, '执行“准备 / 重建”，重新生成对应样本的平台补丁。'),
        ('BUILD', '当前兼容代码编译', lambda p: runtime.runtime_classpath(p, headless=True),
         '执行“准备 / 重建”；旧 alpha.1 兼容层需按本版源码重新编译。'),
        ('ABI', '兼容类方法签名', lambda p: _abi_required(p), '重建兼容层；方法名称、参数、返回类型和静态属性必须匹配。'),
        ('SWT', '固定版本 Linux SWT', runtime.verify_swt,
         '安装锁定且签名有效的 SWT P2 .jar，或显式运行 fetch-swt；旧 SDK ZIP 不作运行构件。'),
    ]
    for code, label, function, action in operations:
        ok = False
        if root is not None:
            try:
                function(root)
                ok = True
            except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile):
                pass
        checks.append(_check(code, ok, label, action))
    return {'schema': 1, 'version': __version__, 'checked_at_utc': utc_now(), 'checks': checks,
            'static_ready': all(c['status'] in ('passed','not_required') for c in checks),
            'native_browser': 'not_tested_by_diagnostics', 'vendor_login': 'not_tested',
            'libraries_detected': {k: bool(v) for k, v in base['libraries'].items()},
            'java_major': base['java']['major'], 'cpu': base['machine'],
            'note': '静态检查通过也不等于原生浏览器、登录或业务报表通过。'}


def diagnose(root: Path | None) -> dict:
    if root is None:return _diagnose(None)
    from .toolchain import execution
    with execution(root):return _diagnose(root)


def setup(root: Path, installer_path: Path | None, sdk: Path | None = None,
          cancel: threading.Event | None = None) -> dict:
    """Prepare or rebuild; no implicit network downloads, no existing-root deletion."""
    steps = []
    if cancel and cancel.is_set():
        return {'status': 'cancelled', 'steps': []}
    if root.exists() or root.is_symlink():
        installer.checked_root(root)
        installer.verify_payload(root)
        steps.append({'stage': 'prepare', 'status': 'reused_verified_root'})
    else:
        if installer_path is None:
            raise ValueError('Select the original installer for a new runtime directory')
        result = installer.prepare(installer_path, root)
        steps.append({'stage': 'prepare', 'status': 'passed', 'files': result['files']})
    with workspace_lock(root):
        from .transactions import pending
        if pending(root):raise ValueError('Recover interrupted transaction before setup')
        if cancel and cancel.is_set():
            return {'status':'cancelled','steps':steps,'original_payload_preserved':True}
        patches.apply(root)
        steps.append({'stage':'patch','status':'passed'})
        from .upgrade import apply_upgrade
        result=apply_upgrade(root,candidate=sdk,code_only=sdk is None)
        steps.append({'stage':'swt','status':'passed' if result['swt_verified'] else 'not_requested'})
        steps.append({'stage':'prebuilt','status':'passed','transaction_status':result['status']})
    return {'status': 'prepared', 'steps': steps, 'diagnostics': diagnose(root),
            'vendor_started': False, 'network_downloaded': False}


PROOFS = {'export': 'ORIGINAL_EXPORT_ROUNDTRIP_PASS',
          'link': 'loaded=486 failed=0',
          'export-matrix': 'EXPORT_MATRIX_PASS files=6 cells=1550',
          'native': 'NATIVE_RENDERER_BRIDGE_EXPORT_PASS',
          'gtk': 'GTK_WIDGET_LIFECYCLE_PASS'}


def proof_passed(kind: str, result: dict) -> bool:
    from .probe_protocol import passed
    return kind in PROOFS and passed(kind,result,PROOFS[kind])


def self_test(root: Path, *, native: bool = True, timeout: float = 30,
              cancel: threading.Event | None = None) -> dict:
    timeout = validate_timeout(timeout)
    root = installer.checked_root(root)
    stages = []
    with workspace_lock(root):
        preflight = diagnose(root)
        for kind in ('export', 'link', 'export-matrix', 'gtk', 'native'):
            if cancel and cancel.is_set():
                stages.append({'stage': kind, 'status': 'cancelled'})
                continue
            if kind in ('gtk','native') and not native:
                stages.append({'stage': kind, 'status': 'not_requested'})
                continue
            graphical_blockers=[x['code'] for x in preflight['checks'] if x['status'] not in ('passed','not_required') and
                                not (kind=='gtk' and x['code']=='WEBKIT')]
            if kind in ('gtk','native') and graphical_blockers:
                stages.append({'stage': kind, 'status': 'blocked','blocker_codes':graphical_blockers})
                continue
            try:
                result = runtime.probe(root, kind, timeout, cancel=cancel)
                stages.append({'stage': kind, 'status': 'passed' if proof_passed(kind, result) else 'failed',
                               'evidence': result})
            except (ValueError, OSError, subprocess.SubprocessError) as ex:
                # Local report only: diagnostic packages deliberately do not copy exception text.
                stages.append({'stage': kind, 'status': 'blocked', 'error': str(ex)})
        headless_ok = all(s['status'] == 'passed' for s in stages[:3])
        gtk_ok=next(s for s in stages if s['stage']=='gtk')['status']=='passed'
        native_ok=gtk_ok and next(s for s in stages if s['stage']=='native')['status']=='passed'
        result = {'schema': 1, 'version': __version__, 'created_at_utc': utc_now(),
                  'stages': stages, 'diagnostics': preflight,
                  'headless_passed': headless_ok, 'gtk_passed':gtk_ok, 'native_passed': native_ok,
                  'overall': 'local_platform_passed' if headless_ok and native_ok else
                      ('headless_passed_native_pending' if headless_ok else 'incomplete'),
                  'vendor_window': 'not_tested', 'vendor_login': 'not_tested',
                  'business_export': 'not_tested', 'full_client_accepted': False}
        filename = f'self-test-{time.time_ns()}.json'
        atomic_json(installer.managed_path(root, 'runs/' + filename), result)
        result['local_report'] = str(root / 'runs' / filename)
        return result


def support_bundle(root: Path, output: Path) -> dict:
    """Export only allowlisted machine checks, not files, logs or user data."""
    root = installer.checked_root(root)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Support archive already exists; choose a new file')
    # Never write through a symlinked directory.
    for p in (output.parent, *output.parent.parents):
        if p.is_symlink():
            raise ValueError('Support destination includes a symlink')
    check = diagnose(root)
    from .signed_swt import URL as SIGNED_URL, SHA256 as SIGNED_SHA256
    contents = {
        'diagnostics.json': json.dumps(check, ensure_ascii=False, indent=2),
        'dependency.json': json.dumps({'swt_signed_p2_url': SIGNED_URL, 'swt_sha256': SIGNED_SHA256,
                                      'certificate_revocation_checked': False}, indent=2),
        'README.txt': ('This is a minimized local port diagnostic package.\n'
                       'No client binaries, source reports, business files, tokens, HOME, paths, raw logs,\n'
                       'screenshots or environment variable values are included.\n'
                       'It is not proof of successful vendor login or production readiness.\n')
    }
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            with zipfile.ZipFile(handle, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, text in contents.items():
                    archive.writestr(name, text)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {'status': 'created', 'output': str(output), 'members': sorted(contents),
            'raw_client_data_included': False, 'review_before_sharing': True}
