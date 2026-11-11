"""Private worker entry: bounded stdin/stdout, no credentials, resource limits first.
This is resource/process isolation, NOT a filesystem or network security sandbox.
"""
from __future__ import annotations
import base64,json,os,resource,sys
from pathlib import Path

MAX_INPUT=12*1024*1024
MAX_OUTPUT=32*1024*1024

def main() -> int:
    # Apply before importing spreadsheet libraries; limits are not upload-configurable.
    resource.setrlimit(resource.RLIMIT_AS,(1024*1024*1024,1024*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(8,9))
    resource.setrlimit(resource.RLIMIT_FSIZE,(MAX_OUTPUT,MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from app.domain import DomainError
    from app.parsers import parse_file
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:raise ValueError('request limit')
        q=json.loads(raw)
        if not isinstance(q,dict) or set(q)!={'data','filename','sheet','header_row'}:raise ValueError('request schema')
        data=base64.b64decode(q['data'],validate=True)
        result=parse_file(data,q['filename'],sheet=q['sheet'],header_row=q['header_row'])
        response={'ok':True,'parsed':result,'resource_isolated':True}
    except DomainError as ex:
        response={'ok':False,'error':{'code':ex.code,'message':ex.message,'status':ex.status}}
    except (ValueError,TypeError,KeyError,MemoryError):
        response={'ok':False,'error':{'code':'parse_worker_failure','message':'解析进程拒绝损坏或超限输入。','status':422}}
    out=json.dumps(response,ensure_ascii=False,separators=(',',':')).encode()
    if len(out)>MAX_OUTPUT:return 3
    sys.stdout.buffer.write(out);sys.stdout.buffer.flush()
    return 0

if __name__=='__main__':raise SystemExit(main())
