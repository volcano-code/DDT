"""Unit tests for launch/diagnostic safety; these are NOT browser E2E evidence."""
import json
import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from browser_runtime import (CHANNELS, DISABLED_SANDBOX_FLAGS, SANDBOX_ERROR_CODES, clean_driver_environment,
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


def current_rows(mechanism='Namespace'):
    # Mirrors Chromium's linuxHandler table, not the pre-2018 row names.
    return [['Layer 1 Sandbox', mechanism], ['PID namespaces', 'Yes'],
            ['Network namespaces', 'Yes'], ['Seccomp-BPF sandbox', 'Yes'],
            ['Seccomp-BPF sandbox supports TSYNC', 'Yes'],
            ['Ptrace Protection with Yama LSM (Broker)', 'Yes'],
            ['Ptrace Protection with Yama LSM (Non-broker)', 'No']]


@pytest.mark.parametrize('rows', [[], [['Namespace sandbox', 'Yes']],
    [['Namespace sandbox', 'Yes'], ['Seccomp-BPF sandbox', 'Yes']],
    [['Layer 1 Sandbox', 'Namespace'], ['Seccomp-BPF sandbox', 'Yes']],
    current_rows('None'), current_rows('Unknown'), current_rows('Namespace SUID'),
    current_rows() + [['Layer 1 Sandbox', 'None']],
    current_rows() + [['Seccomp-BPF sandbox', 'No']],
    current_rows() + [['Layer 1 Sandbox']],
    current_rows() + [[None, 'Yes']], None, 'Yes',
    current_rows() + [['Layer 1 Sandbox', 'Namespace', 'No']]])
def test_sandbox_unknown_or_disabled_fails_closed(rows):
    with pytest.raises(RuntimeError):
        sandbox_status(rows)


@pytest.mark.parametrize('index', [1, 2, 3])
@pytest.mark.parametrize('value', ['No', 'Unknown', 'Yes No', ''])
def test_each_required_protection_must_be_explicitly_enabled(index, value):
    rows = current_rows()
    rows[index][1] = value
    with pytest.raises(RuntimeError, match='sandbox_status_not_verified'):
        sandbox_status(rows)


@pytest.mark.parametrize('mechanism', ['Namespace', 'SUID'])
def test_supported_sandbox_mechanisms(mechanism):
    result = sandbox_status(current_rows(mechanism))
    assert result == {'namespace': mechanism == 'Namespace', 'suid': mechanism == 'SUID',
                      'pid_namespaces': True, 'network_namespaces': True, 'seccomp_bpf': True}


@pytest.mark.parametrize('code', sorted(SANDBOX_ERROR_CODES))
def test_only_exact_sandbox_reason_codes_are_serialized(code):
    assert failure_details('browser_sandbox', RuntimeError(code))['reason'] == code
    result = failure_details('browser_sandbox', RuntimeError(code + ' private-value'))
    assert result['reason'] == 'sandbox_verification_failed'
    assert 'private-value' not in json.dumps(result)


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


@pytest.mark.parametrize('evaluation', ['You are adequately sandboxed.',
    'You are NOT adequately sandboxed.', '', 'You are adequately sandboxed. private'])
def test_runtime_verification_checks_overall_status_and_closes_context(evaluation):
    class Session:
        detached = False
        def send(self, method):
            assert method == 'Browser.getBrowserCommandLine'
            return {'arguments': ['chrome', '--headless', '--enable-automation']}
        def detach(self): self.detached = True
    class Page:
        def goto(self, url, timeout): assert url == 'chrome://sandbox'
        def get_by_text(self, text, exact):
            assert text == 'Seccomp-BPF sandbox' and exact
            return self
        def wait_for(self, timeout): pass
        def locator(self, selector):
            assert selector in {'#sandbox-status tr', '#evaluation'}
            return self
        def evaluate_all(self, _): return current_rows()
        def inner_text(self): return evaluation
    class Context:
        closed = False
        def new_page(self): return Page()
        def close(self): self.closed = True
    class Browser:
        version = '143.0.0.1'
        def __init__(self):
            self.session, self.context = Session(), Context()
        def new_browser_cdp_session(self): return self.session
        def new_context(self): return self.context
    browser = Browser()
    if evaluation == 'You are adequately sandboxed.':
        result = verify_sandbox(browser)
        assert result['adequate_reported'] is True
        assert result['disabling_flags_absent'] is True
        assert result['browser_version'] == browser.version
    else:
        with pytest.raises(RuntimeError, match='sandbox_evaluation_not_verified'):
            verify_sandbox(browser)
    assert browser.session.detached and browser.context.closed
