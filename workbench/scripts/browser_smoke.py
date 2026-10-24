#!/usr/bin/env python3
"""Actual browser -> local HTTP acceptance. No policy changes, API mocks or model calls.

Reports contain allowlisted status/check names only. Ephemeral credentials,
downloads, database and server logs stay in a private temporary directory.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def environment(home: Path) -> dict[str, str]:
    env = {'HOME':str(home), 'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin',
           'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8', 'DDT_STATE_DIR':str(home),
           'PYTHONPATH':str(ROOT/'backend')}
    # A caller may select an installed Playwright cache, not override browser policy.
    if os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
        env['PLAYWRIGHT_BROWSERS_PATH'] = os.environ['PLAYWRIGHT_BROWSERS_PATH']
    return env


def stop(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--chromium', type=Path, help='Optional explicit installed browser; default is Playwright bundled Chromium')
    args = parser.parse_args()
    output = args.report.absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        parser.error('New, non-symlink report path required')
    output.parent.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {'schema':1, 'status':'running', 'checks':checks, 'synthetic_only':True,
              'real_http':True, 'browser_sandbox_disabled':False,
              'browser_policies_modified':False, 'vendor_client_loaded':False,
              'model_called':False, 'business_verified':False,
              'started_at_utc':datetime.now(timezone.utc).isoformat()}
    output.write_text(json.dumps(report, indent=2)); os.chmod(output, 0o600)
    proc = None; stage = 'ordinary_user'; errors = []
    try:
        if os.geteuid() == 0: raise RuntimeError('ordinary_user_required')
        stage = 'compiled_assets'
        if not (ROOT/'web/assets/workbench.js').is_file():
            raise RuntimeError('frontend_build_required')
        from generate_public_fixtures import materialize
        materialize(ROOT/'samples')
        with tempfile.TemporaryDirectory(prefix='ddt-browser-') as td:
            home = Path(td); token = secrets.token_urlsafe(32)
            user = {'token_sha256':hashlib.sha256(token.encode()).hexdigest(),
                    'user_id':'browser-reviewer','tenant':'DEMO','role':'reviewer','accounts':['TEST-001']}
            users = home/'users.json'; users.write_text(json.dumps([user])); users.chmod(0o600)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
            origin = f'http://127.0.0.1:{port}'
            env = environment(home)
            log = (home/'server.log').open('w')
            def start():
                child = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory',
                    '--host','127.0.0.1','--port',str(port),'--no-access-log'],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                try:
                    with httpx.Client(trust_env=False, timeout=2) as client:
                        for _ in range(100):
                            if child.poll() is not None: raise RuntimeError('server_exited')
                            try:
                                if client.get(origin+'/health').status_code == 200: return child
                            except httpx.TransportError: pass
                            time.sleep(.1)
                    raise TimeoutError('server_not_ready')
                except BaseException:
                    stop(child); raise
            try:
                stage = 'http_start'; proc = start(); checks.append('real_http_ready')
                # Limit the Playwright driver itself to the same credential-free environment.
                saved = os.environ.copy(); os.environ.clear(); os.environ.update(env)
                try:
                    with sync_playwright() as pw:
                        stage = 'browser_start'
                        options = {'headless':True, 'chromium_sandbox':True, 'env':env}
                        if args.chromium: options['executable_path'] = str(args.chromium)
                        browser = pw.chromium.launch(**options)
                        try:
                            context = browser.new_context(accept_downloads=True)
                            # Local requests are genuine; never substitute API responses.
                            context.route('**/*', lambda route: route.continue_()
                                if route.request.url.startswith(origin+'/') else route.abort())
                            page = context.new_page(); page.on('pageerror',lambda _: errors.append('page_error'))
                            stage = 'navigation'; page.goto(origin, timeout=15000)
                            expect(page.locator('#login-form')).to_be_visible()
                            page.locator('#token').fill(token); page.locator('#login-form button').click()
                            expect(page.locator('#combined-demo')).to_be_visible(); checks.append('browser_login')
                            stage = 'file_upload'
                            page.locator('[data-tab="imports"]').click()
                            page.locator('#upload-file').set_input_files(str(ROOT/'samples/business.csv'))
                            page.locator('#import-form button').click()
                            expect(page.locator('#confirm-batch')).to_be_enabled(timeout=15000)
                            page.locator('#ack-unknown').check(); page.locator('#confirm-batch').click()
                            expect(page.locator('#confirm-batch')).to_be_disabled(); checks.append('file_picker_upload_and_confirm')
                            page.locator('[data-tab="overview"]').click(); page.locator('#combined-demo').click()
                            expect(page.locator('.detail')).to_have_count(2, timeout=15000)
                            checks.append('combined_demo_real_http_import')
                            stage = 'review'
                            for i in range(2):
                                page.locator('.detail').nth(i).click()
                                expect(page.get_by_role('dialog')).to_be_visible()
                                page.locator('#review-reason').fill('人工核验合成来源和分配')
                                page.locator('#approve').click()
                                expect(page.get_by_role('dialog')).to_have_count(0)
                            checks.append('human_review_shared_fund')
                            stage = 'assistant'
                            page.locator('[data-tab="assistant"]').click()
                            page.locator('#assistant-form > button').click()
                            expect(page.locator('#execute-task')).to_be_visible()
                            checks.append('task_requires_confirmation')
                            page.locator('#execute-task').click()
                            expect(page.locator('.trace')).to_contain_text('facts_rendered')
                            checks.append('local_task_executes_no_model')
                            stage = 'vouchers'
                            page.locator('[data-tab="vouchers"]').click()
                            expect(page.locator('#generate-vouchers')).to_be_disabled()
                            page.locator('#demo-policy').click(); page.locator('#policy-form button').click()
                            expect(page.locator('.policy-decide[data-decision="approve"]')).to_have_count(1)
                            page.locator('#policy-reason').fill('仅批准合成科目映射')
                            page.locator('.policy-decide[data-decision="approve"]').click()
                            expect(page.locator('#generate-vouchers')).to_be_enabled()
                            page.locator('#generate-vouchers').click()
                            expect(page.locator('.open-voucher')).to_have_count(2)
                            page.locator('tbody tr').filter(has_text='C001').locator('.open-voucher').click()
                            page.locator('#voucher-reason').fill('核验合成草稿与来源')
                            page.locator('#review-voucher').click()
                            expect(page.get_by_role('dialog')).to_contain_text('草稿已复核')
                            checks.append('policy_and_voucher_review')
                            stage = 'download'
                            with page.expect_download() as event: page.locator('#export-voucher').click()
                            event.value.save_as(home/'synthetic.csv')
                            text = (home/'synthetic.csv').read_text(encoding='utf-8-sig')
                            assert 'SHA256:' in text and '非正式记账' in text
                            checks.append('actual_browser_download_provenance')
                            stage = 'restart'; stop(proc); proc = start()
                            page.reload(); expect(page.locator('#login-form')).to_be_visible()
                            page.locator('#token').fill(token); page.locator('#login-form button').click()
                            expect(page.locator('#combined-demo')).to_be_visible()
                            page.locator('[data-tab="vouchers"]').click()
                            expect(page.locator('.open-voucher')).to_have_count(2)
                            checks.append('real_server_restart_preserves_drafts')
                            page.locator('#logout').click(); expect(page.locator('#login-form')).to_be_visible()
                            checks.append('logout')
                            assert not errors
                            report['status'] = 'passed'
                        finally: browser.close()
                finally:
                    os.environ.clear(); os.environ.update(saved)
            finally:
                stop(proc); proc = None; log.close()
    except Exception as exc:
        report.update(status='blocked_or_failed', failed_stage=stage, error_type=type(exc).__name__)
        # Never export exception text containing URLs, values or runtime credentials.
        if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc): report['reason'] = 'browser_navigation_policy'
        elif stage == 'browser_start': report['reason'] = 'browser_launch_unavailable'
        else: report['reason'] = 'acceptance_incomplete'
    finally:
        stop(proc)
        report.update(passed_checks=len(checks), page_errors=len(errors),
                      finished_at_utc=datetime.now(timezone.utc).isoformat())
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 2

if __name__ == '__main__':
    raise SystemExit(main())
