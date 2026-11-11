"""Strict local probe evidence. Never turn partial, replayed or duplicate JSON into PASS.

The nonce/PID bind a result to a supervised process; this is not an adversarial
sandbox, remote attestation, or proof of vendor login.
"""
from __future__ import annotations
import json,re
MARKER='DDT_PROBE_JSON='
BOUND_KINDS={'gtk','native','export-matrix'}

def read_measurement(kind: str, result: dict) -> tuple[dict|None,str|None]:
    if kind not in BOUND_KINDS:return None,None
    output=result.get('output')
    if not isinstance(output,str):return None,'non_text_output'
    lines=[x[len(MARKER):] for x in output.splitlines() if x.startswith(MARKER)]
    if len(lines)!=1:return None,'missing_or_duplicate_measurement'
    try:row=json.loads(lines[0])
    except (ValueError,TypeError):return None,'invalid_measurement_json'
    if not isinstance(row,dict):return None,'non_object_measurement'
    if type(row.get('schema')) is not int or row['schema']!=2 or row.get('kind')!=kind:
        return None,'measurement_schema_or_kind'
    pid=result.get('process_pid');rid=result.get('run_id')
    if (type(pid) is not int or pid<=0 or type(row.get('pid')) is not int or row['pid']!=pid
        or not isinstance(rid,str) or re.fullmatch('[0-9a-f]{32}',rid) is None or row.get('run_id')!=rid):
        return None,'measurement_process_binding'
    if row.get('synthetic_only') is not True:return None,'measurement_scope'
    if kind in ('gtk','native') and row.get('vendor_login') is not False:return None,'measurement_scope'
    return row,None

def passed(kind: str, result: dict, marker: str) -> bool:
    if not isinstance(result,dict):return False
    if (type(result.get('returncode')) is not int or result['returncode']!=0
        or result.get('started') is not True or result.get('offline_network_namespace') is not True
        or any(result.get(k) for k in ('timed_out','cancelled','output_limit_reached','output_truncated'))):
        return False
    if not isinstance(result.get('output'),str) or marker not in result['output']:return False
    if kind not in BOUND_KINDS:return True
    row,error=read_measurement(kind,result)
    if error or row!=result.get('measurement') or result.get('measurement_error'):return False
    def number(key,value):return type(row.get(key)) is int and row[key]==value
    if kind=='gtk':
        return (result.get('headless') is False and number('window_cycles',2) and number('events',2)
            and number('shells_remaining',0) and row.get('webkit_tested') is False)
    if kind=='native':
        return (result.get('headless') is False and number('render_cycles',2)
            and all(row.get(k) is True for k in ('bridge_arguments','bridge_return','original_export')))
    return (number('files',6) and number('cells',1550) and number('verified_blank_border_rows',6)
        and row.get('original_exporter') is True and row.get('gui_tested') is False)
