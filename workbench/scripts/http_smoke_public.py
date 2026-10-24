#!/usr/bin/env python3
"""Real HTTP smoke, synthetic data only; no vendor/client/model calls or shared secrets."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib,json,os,secrets,socket,subprocess,sys,tempfile,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();output=args.report.absolute()
    if output.exists() or any(p.is_symlink() for p in (output,*output.parents)):
        parser.error('New regular output path required')
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'schema':1,'status':'running','checks':[],'synthetic_only':True,'real_http':True,
            'vendor_client_loaded':False,'model_called':False,'business_verified':False,
            'started_at_utc':datetime.now(timezone.utc).isoformat()}
    output.write_text(json.dumps(report));output.chmod(0o600)
    proc=None;stage='ordinary_user'
    def stop():
        nonlocal proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        proc=None
    def check(condition,label):
        if not condition:raise AssertionError(label)
        report['checks'].append(label)
    try:
        if os.geteuid()==0:raise RuntimeError('ordinary_user_required')
        from generate_public_fixtures import materialize
        materialize(ROOT/'samples')
        with tempfile.TemporaryDirectory(prefix='ddt-public-http-') as td:
            home=Path(td);token=secrets.token_urlsafe(32)
            users=home/'users.json';users.write_text(json.dumps([{'token_sha256':hashlib.sha256(token.encode()).hexdigest(),
                'user_id':'synthetic-reviewer','tenant':'DEMO','role':'reviewer','accounts':['TEST-001']}]))
            users.chmod(0o600)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={'HOME':str(home),'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin','LANG':'C.UTF-8',
                 'PYTHONPATH':str(ROOT/'backend'),'DDT_STATE_DIR':str(home)}
            with (home/'server.log').open('w') as log,httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=15) as c:
                def start():
                    nonlocal proc
                    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1',
                        '--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
                    for _ in range(100):
                        if proc.poll() is not None:raise RuntimeError('server_exited')
                        try:
                            if c.get('/health').status_code==200:return
                        except httpx.TransportError:pass
                        time.sleep(.1)
                    raise TimeoutError('not_ready')
                try:
                    stage='start';start();check(True,'real_server_health')
                    check(c.get('/').status_code==200 and c.get('/assets/workbench.js').status_code==200,'built_frontend_served')
                    check(c.get('/api/meta').status_code==401,'authentication_required')
                    c.headers['Authorization']='Bearer '+token
                    check(c.get('/api/meta').json()['external_api']=='not_configured','vendor_not_configured')
                    stage='upload'
                    for filename,kind in [('business.xlsx','business'),('funds.xls','fund')]:
                        r=c.post('/api/batches/preview',files={'file':(filename,(ROOT/'samples'/filename).read_bytes())},
                            data={'options':json.dumps({'kind':kind})})
                        check(r.status_code==200 and r.json()['valid_count']==9,'upload_'+kind)
                        b=r.json();r=c.post('/api/batches/'+b['id']+'/confirm',json={'fingerprint':b['fingerprint'],
                                                                                   'acknowledge_unknown_completeness':True})
                        check(r.status_code==200,'confirm_'+kind)
                    stage='reconcile';r=c.post('/api/reconcile',json={});check(r.status_code==200,'reconcile_http')
                    run=r.json();check(len(run['proposals'])==9 and len(run['fund_balances'])==9,'source_rows_retained')
                    p=next(p for p in run['proposals'] if p['business']['data']['record_id']=='E001')
                    request={'expected_version':p['version'],'decision':'approve','reason':'人工核验合成来源','idempotency_key':'http-public-review'}
                    r=c.post('/api/proposals/'+p['id']+'/review',json=request);check(r.status_code==200,'explicit_human_review')
                    check(c.post('/api/proposals/'+p['id']+'/review',json=request).json()['duplicate'],'review_idempotency')
                    check(c.get('/api/runs/'+run['id']).json()['fund_summary']['allocated']=='10600.00','allocated_amount_from_rules')
                    r=c.get('/api/runs/'+run['id']+'/export');check(r.status_code==200 and 'SHA256:' in r.text and '非正式记账' in r.text,'actual_http_download')
                    stage='assistant';r=c.post('/api/assistant/tasks',json={'run_id':run['id'],'query':'汇总当前核对结果','idempotency_key':'http-public-task'})
                    task=r.json();check(r.status_code==200 and task['status']=='awaiting_confirmation','task_requires_confirmation')
                    r=c.post('/api/assistant/tasks/'+task['id']+'/execute',json={'expected_version':task['version']})
                    check(r.status_code==200 and r.json()['status']=='completed','local_task_no_model')
                    stage='restart';stop();start()
                    check(c.get('/api/assistant/tasks/'+task['id']).json()['status']=='completed','real_restart_persistence')
                    check(c.post('/api/assistant/tasks/'+task['id']+'/execute',json={'expected_version':task['version']}).json()['duplicate'],'restart_idempotency')
                    stage='correction';r=c.post('/api/batches/preview',files={'file':('business_correction.csv',(ROOT/'samples/business_correction.csv').read_bytes())},data={'options':'{"kind":"business"}'})
                    b=r.json();r=c.post('/api/batches/'+b['id']+'/confirm',json={'fingerprint':b['fingerprint'],'acknowledge_unknown_completeness':True})
                    check(r.status_code==200 and c.get('/api/runs/'+run['id']).json()['stale'],'correction_invalidates_old_result')
                    report['status']='passed'
                finally:stop()
    except Exception as exc:
        report.update(status='failed',failed_stage=stage,error_type=type(exc).__name__)
    finally:
        stop();report.update(passed_checks=len(report['checks']),finished_at_utc=datetime.now(timezone.utc).isoformat())
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
