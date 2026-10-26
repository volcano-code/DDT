"""Unit tests for launch/diagnostic safety; these are NOT browser E2E evidence."""
import json
import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from browser_runtime import (CHANNELS, DISABLED_SANDBOX_FLAGS, clean_driver_environment,
                             failure_details, launch_options, sandbox_flags_enabled,
                             sandbox_status, system_observations, verify_sandbox)


@pytest.mark.parametrize('channel', CHANNELS)
def test_launch_always_enables_sandbox(channel):
    env = {'HOME': '/synthetic'}
    options = launch_options(channel, env)
    assert options['chromium_sandbox'] is True
    assert options['headless'] is True
    assert options['timeout'] == 15000
    assert 'args' not in options and 'ignore_default_args' not in options
    assert options['env'] == env and options['env'] is not env
    assert options.get('channel') == ('chrome' if channel == 'chrome' else None)


@pytest.mark.parametrize('channel,executable', [('unknown', None), ('chrome', Path('/synthetic'))])
def test_ambiguous_browser_selection_rejected(channel, executable):
    with pytest.raises(ValueError):
        launch_options(channel, {}, executable)


def test_explicit_browser_keeps_sandbox():
    opts = launch_options('bundled', {}, Path('/synthetic/browser'))
    assert opts['executable_path'] == '/synthetic/browser'
    assert opts['chromium_sandbox'] is True and 'channel' not in opts


@pytest.mark.parametrize('flag', sorted(DISABLED_SANDBOX_FLAGS))
def test_disabling_flags_rejected(flag):
    assert not sandbox_flags_enabled(['chrome', flag])
    assert not sandbox_flags_enabled(['chrome', flag + '=true'])
    assert sandbox_flags_enabled(['chrome', '--headless', '--enable-automation'])


@pytest.mark.parametrize('stage,message,reason', [
    ('navigation', 'net::ERR_BLOCKED_BY_ADMINISTRATOR', 'browser_navigation_policy'),
    ('browser_start', 'FATAL No usable sandbox!', 'no_usable_sandbox'),
    ('browser_start', 'Failed to move to new namespace: Operation not permitted', 'namespace_creation_denied'),
    ('browser_start', 'Failed to unshare: EPERM', 'namespace_creation_denied'),
    ('browser_start', "Executable doesn't exist at /private/cache", 'browser_executable_missing'),
    ('browser_start', "Chromium distribution 'chrome' is not found", 'browser_executable_missing'),
    ('browser_start', 'error while loading shared libraries: libX.so', 'browser_shared_library_missing'),
    ('browser_start', 'Running as root without --no-sandbox is not supported', 'root_browser_refused'),
    ('browser_sandbox', 'unknown status', 'sandbox_verification_failed'),
    ('browser_start', 'unrecognized launch failure', 'browser_launch_unavailable'),
    ('review', 'unrecognized failure', 'acceptance_incomplete'),
])
def test_allowlisted_failure_details(stage, message, reason):
    marker = 'private-token-DO-NOT-LOG'
    details = failure_details(stage, RuntimeError(message + ' https://private.invalid/' + marker))
    assert details == {'reason': reason, 'error_type': 'RuntimeError'}
    assert marker not in json.dumps(details) and 'https' not in json.dumps(details)


def test_untrusted_exception_class_name_not_exported():
    custom = type('sensitive_customer_name', (Exception,), {})
    assert failure_details('review', custom('private'))['error_type'] == 'OtherError'


@pytest.mark.parametrize('rows', [[], [['Namespace sandbox', 'Yes']],
    [['Namespace sandbox', 'Yes'], ['Seccomp-BPF sandbox', 'No']],
    [['Namespace sandbox', 'No'], ['SUID sandbox', 'No'], ['Seccomp-BPF sandbox', 'Yes']],
    [['Namespace sandbox', 'Yes No'], ['Seccomp-BPF sandbox', 'Yes']],
    [['Namespace sandbox', 'Yes'], ['Namespace sandbox', 'No'], ['Seccomp-BPF sandbox', 'Yes']]])
def test_sandbox_unknown_or_disabled_fails_closed(rows):
    with pytest.raises(RuntimeError):
        sandbox_status(rows)


@pytest.mark.parametrize('mechanism', ['Namespace sandbox', 'SUID sandbox'])
def test_supported_sandbox_mechanisms(mechanism):
    assert sandbox_status([[mechanism, 'Yes'], ['Seccomp-BPF sandbox', 'Yes']])['seccomp_bpf'] is True


def test_driver_environment_restored_even_on_interrupt(monkeypatch):
    monkeypatch.setenv('DDT_TEST_SECRET', 'do-not-inherit')
    before = dict(os.environ)
    with pytest.raises(KeyboardInterrupt):
        with clean_driver_environment({'HOME': '/synthetic'}):
            assert dict(os.environ) == {'HOME': '/synthetic'}
            raise KeyboardInterrupt()
    assert dict(os.environ) == before


def test_observations_have_no_paths_or_environment_secrets():
    result = system_observations()
    assert set(result) == {'ordinary_user', 'apparmor_userns_restriction', 'unprivileged_userns_clone',
                           'system_chrome_present', 'chrome_profile_file_present'}
    assert set(result.values()) <= {True, False, '0', '1', 'unknown'}


def test_runtime_verification_rejects_disabled_sandbox_before_page():
    class Session:
        detached = False
        def send(self, _): return {'arguments': ['chrome', '--no-sandbox']}
        def detach(self): self.detached = True
    class Browser:
        session = Session()
        def new_browser_cdp_session(self): return self.session
        def new_context(self): raise AssertionError('must not navigate')
    browser = Browser()
    with pytest.raises(RuntimeError, match='sandbox_disabling_flag_detected'):
        verify_sandbox(browser)
    assert browser.session.detached
