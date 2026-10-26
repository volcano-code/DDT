"""Fail-closed browser launch helpers; diagnostics never include raw exception text."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import tempfile

CHANNELS = ('bundled', 'chrome')
DISABLED_SANDBOX_FLAGS = frozenset({
    '--no-sandbox', '--disable-setuid-sandbox', '--disable-namespace-sandbox',
    '--disable-seccomp-filter-sandbox', '--disable-gpu-sandbox', '--no-zygote',
    '--single-process',
})


def launch_options(channel: str, env: dict[str, str], executable: Path | None = None) -> dict:
    if channel not in CHANNELS or (executable is not None and channel != 'bundled'):
        raise ValueError('invalid_browser_selection')
    result = {'headless': True, 'chromium_sandbox': True, 'env': dict(env), 'timeout': 15000}
    if executable is not None:
        result['executable_path'] = str(executable)
    elif channel == 'chrome':
        result['channel'] = 'chrome'
    return result


def failure_details(stage: str, exc: Exception) -> dict[str, str]:
    # Match recognizable messages in memory. Never serialize text/URLs/paths/tokens.
    text = str(exc).lower()
    if 'err_blocked_by_administrator' in text:
        reason = 'browser_navigation_policy'
    elif 'no usable sandbox' in text:
        reason = 'no_usable_sandbox'
    elif 'failed to move to new namespace' in text or 'failed to unshare' in text:
        reason = 'namespace_creation_denied'
    elif 'running as root without --no-sandbox' in text:
        reason = 'root_browser_refused'
    elif 'executable doesn\'t exist' in text or 'distribution' in text and 'not found' in text:
        reason = 'browser_executable_missing'
    elif 'error while loading shared libraries' in text:
        reason = 'browser_shared_library_missing'
    elif stage == 'browser_sandbox':
        reason = 'sandbox_verification_failed'
    elif stage == 'browser_start':
        reason = 'browser_launch_unavailable'
    else:
        reason = 'acceptance_incomplete'
    error_type = type(exc).__name__
    if error_type not in {'Error', 'TimeoutError', 'TargetClosedError', 'RuntimeError',
                          'AssertionError', 'ValueError', 'OSError', 'FileNotFoundError'}:
        error_type = 'OtherError'
    return {'reason': reason, 'error_type': error_type}


def system_observations() -> dict:
    def bit(path: str) -> str:
        try:
            value = Path(path).read_text().strip()
        except OSError:
            return 'unknown'
        return value if value in {'0', '1'} else 'unknown'
    return {
        'ordinary_user': os.geteuid() != 0,
        'apparmor_userns_restriction': bit('/proc/sys/kernel/apparmor_restrict_unprivileged_userns'),
        'unprivileged_userns_clone': bit('/proc/sys/kernel/unprivileged_userns_clone'),
        # Presence is not a claim that an AppArmor profile is loaded/enforcing.
        'system_chrome_present': Path('/opt/google/chrome/chrome').is_file(),
        'chrome_profile_file_present': Path('/etc/apparmor.d/chrome').is_file(),
    }


def sandbox_flags_enabled(arguments: list[str]) -> bool:
    return not any(arg.split('=', 1)[0] in DISABLED_SANDBOX_FLAGS for arg in arguments)


def sandbox_status(rows: list[list[str]]) -> dict[str, bool]:
    # Require actual status rows, not a substring such as "Yes ... No".
    statuses = {}
    for row in rows:
        if len(row) == 2:
            key = row[0].strip().lower()
            if key in statuses:
                raise RuntimeError('duplicate_sandbox_status')
            statuses[key] = row[1].strip().lower() == 'yes'
    result = {
        'namespace': statuses.get('namespace sandbox', False),
        'suid': statuses.get('suid sandbox', False),
        'seccomp_bpf': statuses.get('seccomp-bpf sandbox', False),
    }
    if not result['seccomp_bpf'] or not (result['namespace'] or result['suid']):
        raise RuntimeError('sandbox_status_not_verified')
    return result


def verify_sandbox(browser) -> dict:
    session = browser.new_browser_cdp_session()
    try:
        arguments = session.send('Browser.getBrowserCommandLine').get('arguments')
        if not isinstance(arguments, list) or not arguments or not all(isinstance(x, str) for x in arguments):
            raise RuntimeError('browser_arguments_unavailable')
        if not sandbox_flags_enabled(arguments):
            raise RuntimeError('sandbox_disabling_flag_detected')
    finally:
        session.detach()
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto('chrome://sandbox', timeout=10000)
        page.get_by_text('Seccomp-BPF sandbox', exact=True).wait_for(timeout=10000)
        rows = page.locator('tr').evaluate_all(
            '(rows) => rows.map(r => Array.from(r.cells, c => c.textContent.trim()))')
        result = sandbox_status(rows)
    finally:
        context.close()
    version = browser.version
    result['browser_version'] = version if re.fullmatch(r'\d+(?:\.\d+){1,3}', version) else 'unknown'
    result['disabling_flags_absent'] = True
    return result


@contextmanager
def clean_driver_environment(env: dict[str, str]):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def main() -> int:
    parser = argparse.ArgumentParser(description='Read-only launch diagnosis; not workbench acceptance.')
    parser.add_argument('--channel', choices=CHANNELS, required=True)
    args = parser.parse_args()
    result = {'schema': 1, 'kind': 'browser_environment_probe', 'channel': args.channel,
              'acceptance_passed': False, 'policies_modified': False,
              'observations': system_observations()}
    stage = 'ordinary_user'
    try:
        if os.geteuid() == 0:
            raise RuntimeError('ordinary_user_required')
        with tempfile.TemporaryDirectory(prefix='ddt-browser-probe-') as directory:
            env = {'HOME': directory, 'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin',
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
            if os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
                env['PLAYWRIGHT_BROWSERS_PATH'] = os.environ['PLAYWRIGHT_BROWSERS_PATH']
            with clean_driver_environment(env):
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    stage = 'browser_start'
                    browser = pw.chromium.launch(**launch_options(args.channel, env))
                    try:
                        stage = 'browser_sandbox'
                        result['sandbox'] = verify_sandbox(browser)
                        result['status'] = 'sandbox_verified'
                    finally:
                        browser.close()
    except Exception as exc:
        result.update(status='blocked_or_failed', failed_stage=stage, **failure_details(stage, exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'sandbox_verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
