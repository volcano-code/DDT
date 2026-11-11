"""Conservative interpretation of launcher evidence; exit zero is not business success."""
from __future__ import annotations
import json,re

def classify(result:dict)->dict:
    out=result.get('output','');events=[]
    allowed={'bootstrap_entered','integrity_verified','native_preflight_passed','original_main_entered',
             'original_main_returned','original_main_exception','jvm_shutdown','owned_display_cleanup_completed'}
    for line in out.splitlines():
        if line.startswith('DDT_STARTUP_EVENT='):
            try:
                e=json.loads(line.split('=',1)[1])
                if isinstance(e,dict) and e.get('phase') in allowed:events.append(e['phase'])
            except (ValueError,TypeError):pass
    categories=[]
    patterns={
      'windows_process_dependency':r'Cannot run program "(?:cmd\.exe|powershell\.exe)"',
      'null_network_interface':r'netInterface" is null|getHardwareAddress.*null',
      'jar_signature_error':r'Invalid signature file digest',
      'java_exception':r'(?:java|javax|org\.eclipse)\.[\w.$]*(?:Exception|Error)\b',
      'native_warning':r'Gdk-CRITICAL|Gtk-CRITICAL',
    }
    for name,pattern in patterns.items():
        if re.search(pattern,out):categories.append(name)
    from .window_evidence import summarize
    window=summarize(result)
    if window.get('observer_error') or window.get('error'):categories.append('window_observation_invalid')
    if result.get('preflight_blocked'):state='native_preflight_failed'
    elif result.get('cancelled'):state='cancelled_by_controller'
    elif result.get('timed_out'):state='timed_out'
    elif result.get('output_limit_reached'):state='output_limited'
    elif any(c!='native_warning' for c in categories) or result.get('returncode') not in (0,None):state='startup_error'
    elif window.get('shutdown_seen') and not window.get('main_returned'):
        state='jvm_shutdown_without_main_return_reason_unverified'
    elif 'original_main_returned' in events:state='original_main_returned_business_unverified'
    elif 'original_main_entered' in events or 'entering original client Start.main' in out:state='process_ended_reason_unverified'
    elif result.get('started'):state='process_started_entry_unverified'
    else:state='not_started'
    # Mechanism is an independently decoded private recording, not a log-string guess.
    exit_record={'status':'unavailable','mechanism':'unknown','business_verified':False}
    supplied=result.get('exit_evidence')
    if isinstance(supplied,dict) and supplied.get('status')=='verified_recording':
        from .exit_evidence import validate
        fields=('schema','run_id','pid','mechanism','identity_bound','shutdown_event_present','frames','business_verified')
        try:
            exit_record=validate({k:supplied[k] for k in fields},run_id=result.get('run_id',''),pid=result.get('process_pid'))
            exit_record['status']='verified_recording'
        except (KeyError,TypeError,ValueError):
            exit_record={'status':'rejected','mechanism':'unknown','business_verified':False}
        if state in ('jvm_shutdown_without_main_return_reason_unverified','process_ended_reason_unverified'):
            if exit_record['mechanism']=='explicit_system_exit':state='explicit_jvm_exit_business_unverified'
            elif exit_record['mechanism']=='signal_handler_shutdown':state='signal_shutdown_business_unverified'
    # No exit mechanism ever proves the application is correct or authenticated.
    external=result.get('native_preflight',{})
    if external.get('proof_passed') is True and external.get('isolated_jvm') is True:
        events.insert(0,'native_preflight_passed_external_jvm')
    return {'schema':3,'classification':state,'stages':events,'issues':categories,
            'window_observed':window['window_observed'],'window_evidence':window,
            'exit_evidence':exit_record,'closed_by_user':'unknown','vendor_login_verified':False,'business_compatible':False,
            'diagnostic_contains_business_values':False,
            'note':'Original client log text and returncode do not prove a visible window, login or normal exit.'}
