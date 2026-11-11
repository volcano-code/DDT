"""Two-process native-preflight then original-client launch; no login bypass.

The preflight is not a state/attestation passed to BrowserHealth inside the client.
Original adapter calls still perform their own checks, using the vendor Display.
Moving the launcher's initial Display out of the client JVM avoids that specific
create/dispose/create sequence. Real GTK/GDK behaviour still needs GUI acceptance.
"""
from __future__ import annotations
import json,time
from typing import Callable

MARKER='DDT_NATIVE_PREFLIGHT_JSON='

def preflight_proof(result:dict)->bool:
    if (result.get('returncode')!=0 or result.get('started') is not True
            or any(result.get(k) for k in ('cancelled','timed_out','output_limit_reached','output_truncated'))):
        return False
    lines=[line[len(MARKER):] for line in result.get('output','').splitlines() if line.startswith(MARKER)]
    if len(lines)!=1:return False
    try:value=json.loads(lines[0])
    except (ValueError,TypeError):return False
    if not isinstance(value,dict):return False
    return (value.get('schema')==1 and value.get('kind')=='browser_preflight'
            and value.get('native_browser_created') is True and value.get('vendor_started') is False
            and type(value.get('pid')) is int and value['pid']==result.get('process_pid'))

def run(run_stage:Callable,*,timeout:float,cancel=None,clock=None)->dict:
    """The caller owns one workspace lock and one tool selection for both JVMs.

    run_stage accepts (main_class, bounded_or_zero_timeout). Tests may substitute
    this seam, but a substituted result is never a real browser acceptance record.
    """
    clock=clock or time.monotonic
    start=clock();cap=30.0 if timeout==0 else min(30.0,timeout)
    pre=run_stage('port.BrowserPreflight',cap)
    passed=preflight_proof(pre)
    envelope={'proof_passed':passed,'isolated_jvm':True,'result':pre,
              'scope':'actual_native_creation_not_vendor_login'}
    if not passed:
        return {'returncode':pre.get('returncode') or 2,'started':False,
                'cancelled':bool(pre.get('cancelled')),'timed_out':bool(pre.get('timed_out')),
                'output_limit_reached':bool(pre.get('output_limit_reached')),
                'output_truncated':False,'output':'Native preflight failed; original client was NOT started.',
                'elapsed_seconds':round(clock()-start,3),'preflight_blocked':True,
                'native_preflight':envelope,'vendor_process_started':False,'timeout_scope':'entire_launch'}
    elapsed=clock()-start
    if cancel is not None and cancel.is_set():
        return {'returncode':None,'started':False,'cancelled':True,'timed_out':False,
                'output_limit_reached':False,'output_truncated':False,
                'output':'Cancelled after native preflight; original client was NOT started.',
                'elapsed_seconds':round(elapsed,3),'native_preflight':envelope,
                'vendor_process_started':False,'timeout_scope':'entire_launch'}
    if timeout and elapsed>=timeout:
        return {'returncode':2,'started':False,'cancelled':False,'timed_out':True,
                'output_limit_reached':False,'output_truncated':False,
                'output':'Total launch deadline reached after preflight; original client was NOT started.',
                'elapsed_seconds':round(elapsed,3),'native_preflight':envelope,
                'vendor_process_started':False,'timeout_scope':'entire_launch'}
    remaining=0.0 if timeout==0 else timeout-elapsed
    result=run_stage('port.Bootstrap',remaining)
    return {**result,'elapsed_seconds':round(clock()-start,3),'native_preflight':envelope,
            'vendor_process_started':result.get('started') is True,'timeout_scope':'entire_launch'}
