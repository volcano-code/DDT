"""Repeatable native acceptance runner, never a provisioning or login script.

Native platform success is a separate gate from startup, business and RC release.
Reports are fresh, owner-only, version-bound and do not contain client logs/titles.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,os,platform,stat,tempfile,uuid
from pathlib import Path
from . import __version__,runtime,validation,toolchain,dependencies,signed_swt,prebuilt,installer,patches
from .lifecycle import workspace_lock
from .transactions import safe_file

def now():return datetime.now(timezone.utc).isoformat()

def identity(root:Path)->dict:
    blob,build=prebuilt.read(runtime.PROJECT)
    own=[]
    for prefix in ('ddt_port','java','ci'):
        own += [p for p in sorted((runtime.PROJECT/prefix).rglob('*')) if p.is_file() and p.suffix in ('.py','.java') and '__pycache__' not in p.parts]
    content=b''.join(p.relative_to(runtime.PROJECT).as_posix().encode()+b'\0'+p.read_bytes() for p in sorted(own))
    return {'version':__version__,'tooling_sha256':hashlib.sha256(content).hexdigest(),
            'source_sha256':build['source_sha256'],'prebuilt_sha256':hashlib.sha256(blob).hexdigest(),
            'client_sha256':installer.CLIENT_SHA256,'signed_swt_sha256':signed_swt.SHA256}

class EvidenceJournal:
    """A killed run stays `running`, never reuses a previous PASS. No raw business log."""
    def __init__(self,path:Path):
        self.path=path.absolute();safe_file(self.path,optional=True)
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.close(fd);self.previous=b''
    def write(self,value:dict):
        safe_file(self.path)
        if self.path.read_bytes()!=self.previous:raise ValueError('Evidence changed outside this run')
        raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
        with tempfile.NamedTemporaryFile(prefix='.native-report-',dir=self.path.parent,delete=False) as f:
            temp=Path(f.name)
            try:f.write(raw);f.flush();os.fsync(f.fileno());os.chmod(temp,0o600)
            except BaseException:temp.unlink(missing_ok=True);raise
        try:os.replace(temp,self.path)
        finally:temp.unlink(missing_ok=True)
        self.previous=raw

def process_summary(result:dict)->dict:
    # No output, filenames, titles, URLs, account data or filesystem state is copied.
    names=('returncode','started','cancelled','timed_out','output_limit_reached','output_truncated',
           'elapsed_seconds','process_pid','offline_network_namespace','headless','run_id','measurement_error')
    out={k:result[k] for k in names if k in result}
    measure=result.get('measurement')
    if isinstance(measure,dict):
        allowed=('schema','kind','pid','run_id','window_cycles','events','shells_remaining','render_cycles',
                 'bridge_arguments','bridge_return','original_export','synthetic_only','vendor_login','webkit_tested')
        out['measurement']={k:measure[k] for k in allowed if k in measure}
    if 'startup' in result:
        from .startup import classify
        out['startup']=classify(result)
    pre=result.get('native_preflight')
    if isinstance(pre,dict):
        from .launch_sequence import preflight_proof
        evidence=pre.get('result',{})
        out['preflight']={'proof_passed':preflight_proof(evidence),'isolated_jvm':pre.get('isolated_jvm') is True,
            'process_pid':evidence.get('process_pid'),'returncode':evidence.get('returncode')}
    return out

def run(root:Path,output:Path,*,original_client:bool=False)->dict:
    root=root.absolute();journal=EvidenceJournal(output)
    report={'schema':2,'run_id':uuid.uuid4().hex,'status':'running','started_at_utc':now(),
        'source_identity':None,'environment':{},'diagnostics':None,'signature':None,
        'native_runs':[],'original_runs':[],'native_platform_passed':False,'startup_gate_passed':False,
        'vendor_login_verified':False,'release_candidate_allowed':False,
        'formal_signed_swt_used':False,'raw_client_logs_included':False,
        'target_policy_or_gpu_acceptance':False}
    journal.write(report)
    stage='environment'
    try:
        osinfo=dependencies.os_release()
        apparmor=Path('/sys/module/apparmor/parameters/enabled')
        try:apparmor_enabled=apparmor.read_text().strip()=='Y'
        except OSError:apparmor_enabled=None
        report['environment']={'distribution':osinfo.get('ID'),'release':osinfo.get('VERSION_ID'),
            'kernel':platform.release(),'machine':platform.machine(),'uid':os.geteuid(),
            'apparmor_enabled':apparmor_enabled,'target_host_policy_verified':False}
        if osinfo.get('ID')!='ubuntu' or osinfo.get('VERSION_ID')!='24.04' or platform.machine()!='x86_64' or os.geteuid()==0:
            report.update(status='blocked',blocked_stage=stage,reason='requires_ubuntu24_04_x86_64_normal_user')
        else:
            with workspace_lock(root),toolchain.execution(root):
                stage='integrity';report['source_identity']=identity(root)
                stage='diagnostics';report['diagnostics']=validation.diagnose(root)
                if not report['diagnostics']['static_ready']:
                    report.update(status='blocked',blocked_stage=stage,reason='native_prerequisites_not_ready')
                else:
                    stage='signature';signed_swt.verify_installed(root)
                    report['signature']=signed_swt.strict_verify(root/'native/swt.jar',Path(toolchain.selected_java()['home']))
                    if report['signature'].get('passed') is not True or report['signature'].get('strict') is not True:
                        raise ValueError('Strict signature not verified')
                    report['formal_signed_swt_used']=True
                    journal.write(report)
                    stage='native_probes';seen_runs=set()
                    for iteration in range(5):
                        for kind in ('gtk','native'):
                            actual=runtime.probe(root,kind)
                            ok=validation.proof_passed(kind,actual)
                            if actual.get('run_id') in seen_runs:raise ValueError('Replayed native run evidence')
                            seen_runs.add(actual.get('run_id'))
                            report['native_runs'].append({'iteration':iteration,'kind':kind,'passed':ok,'evidence':process_summary(actual)})
                            journal.write(report)
                            if not ok:raise ValueError('Native probe failed')
                    if identity(root)!=report['source_identity']:raise ValueError('Source identity changed during gate')
                    installer.verify_payload(root);patches.verify(root);signed_swt.verify_installed(root)
                    report['native_platform_passed']=len(report['native_runs'])==10 and all(x['passed'] for x in report['native_runs'])
                    if original_client:
                        stage='original_client'
                        for iteration in range(5):
                            actual=runtime.run_java(root,'port.Bootstrap',[],headless=False,offline=True,timeout=60)
                            if not actual.get('run_id') or actual['run_id'] in seen_runs:raise ValueError('Replayed original run evidence')
                            seen_runs.add(actual['run_id'])
                            summary=process_summary(actual);report['original_runs'].append({'iteration':iteration,'evidence':summary})
                            journal.write(report)
                            pre=summary.get('preflight',{});startup=summary.get('startup',{})
                            if (not pre.get('proof_passed') or not pre.get('isolated_jvm')
                                or pre.get('process_pid')==summary.get('process_pid')
                                or summary.get('offline_network_namespace') is not True):
                                raise ValueError('Original client preflight/process evidence missing')
                            # Offline absence of vendor services is not automatically a failure,
                            # but neither a timeout nor an unexplained return is an accepted startup.
                            if (summary.get('returncode')!=0 or summary.get('started') is not True or summary.get('headless') is not False
                                or any(summary.get(k) for k in ('timed_out','cancelled','output_limit_reached','output_truncated'))
                                or startup.get('issues') or 'original_main_returned' not in startup.get('stages',[])
                                or not startup.get('window_observed') or not startup.get('window_evidence',{}).get('main_returned')):
                                report['original_startup_review_required']=True
                                break
                        report['startup_gate_passed']=len(report['original_runs'])==5 and not report.get('original_startup_review_required',False)
                    if identity(root)!=report['source_identity']:raise ValueError('Source identity changed during gate')
                    installer.verify_payload(root);patches.verify(root);signed_swt.verify_installed(root)
                    report['status']='completed'
                    report['reason']='native_platform_only_not_install_or_business_acceptance'
    except KeyboardInterrupt:
        report.update(status='cancelled',failed_stage=stage,reason='cancelled_by_controller')
    except Exception as ex:
        # Exception messages may contain local/client values. Keep type and stage only.
        report.update(status='failed',failed_stage=stage,error_type=type(ex).__name__,reason='gate_failed_no_success_inferred')
    report['finished_at_utc']=now();journal.write(report)
    return report

def exit_code(report:dict,*,original_client:bool=False)->int:
    if report.get('status')=='cancelled':return 130
    if report.get('status')!='completed' or not report.get('native_platform_passed'):return 2
    if original_client and not report.get('startup_gate_passed'):return 2
    return 0
