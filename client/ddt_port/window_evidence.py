"""Sanitized local window observations, bound to the supervised JVM.
A visible SWT Shell is not an authenticated vendor page. No raw text is exported.
"""
from __future__ import annotations
import json,re
MARKER='DDT_WINDOW_OBSERVATION_JSON='
STAGES={'attached','window_visible','observer_error','main_finished','shutdown'}
KEYS={'schema','pid','run_id','sequence','stage','source','window_observed','visible_shells_peak',
      'close_events','dispose_events','main_returned','observer_error','display_size_known',
      'display_minimum_met','vendor_login_verified'}

def summarize(result: dict) -> dict:
    empty={'window_observed':False,'source':'not_collected','record_count':0,'valid':False,
           'closed_by_user':'unknown','login_page_identified':False,'vendor_login_verified':False}
    output=result.get('output','')
    if not isinstance(output,str):return {**empty,'error':'invalid_output'}
    lines=[line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if not lines:return empty
    if len(lines)>32 or result.get('output_truncated'):return {**empty,'error':'incomplete_observation'}
    rid=result.get('run_id');pid=result.get('process_pid')
    if not isinstance(rid,str) or re.fullmatch('[0-9a-f]{32}',rid) is None or type(pid) is not int or pid<=0:
        return {**empty,'error':'missing_process_identity'}
    rows=[]
    for n,line in enumerate(lines,1):
        try:r=json.loads(line)
        except ValueError:return {**empty,'error':'invalid_json'}
        if not isinstance(r,dict) or set(r)!=KEYS:return {**empty,'error':'invalid_schema'}
        if (type(r['schema']) is not int or r['schema']!=2 or r['run_id']!=rid or type(r['pid']) is not int or r['pid']!=pid
            or type(r['sequence']) is not int or r['sequence']!=n or not isinstance(r['stage'],str) or r['stage'] not in STAGES
            or r['source']!='swt_shell_visibility' or r['vendor_login_verified'] is not False):
            return {**empty,'error':'scope_or_process_binding'}
        for key in ('visible_shells_peak','close_events','dispose_events'):
            if type(r[key]) is not int or not 0<=r[key]<=1_000_000:return {**empty,'error':'invalid_counter'}
            if rows and r[key]<rows[-1][key]:return {**empty,'error':'counter_regression'}
        for key in ('window_observed','main_returned','observer_error','display_size_known','display_minimum_met'):
            if type(r[key]) is not bool:return {**empty,'error':'invalid_boolean'}
        if r['window_observed']!=(r['visible_shells_peak']>0):return {**empty,'error':'visibility_inconsistent'}
        if rows and any(rows[-1][k] and not r[k] for k in ('window_observed','main_returned','observer_error')):
            return {**empty,'error':'state_regression'}
        rows.append(r)
    last=rows[-1]
    return {**empty,'valid':True,'source':'swt_shell_visibility','record_count':len(rows),
        'window_observed':last['window_observed'],'visible_shells_peak':last['visible_shells_peak'],
        'close_events':last['close_events'],'dispose_events':last['dispose_events'],
        'main_returned':last['main_returned'],'observer_error':last['observer_error'],
        'display_size_known':last['display_size_known'],'display_minimum_met':last['display_minimum_met'],
        'shutdown_seen':last['stage']=='shutdown'}
