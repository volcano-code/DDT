#!/usr/bin/env python3
"""Run on an admin-provisioned Ubuntu 24.04 normal-user runner.
No package installation, policy modifications, vendor login or network fallback.
Exit zero means the requested native gate passed, never that an RC is released.
"""
from pathlib import Path
import argparse,json,sys
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from ddt_port.native_acceptance import run,exit_code

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--original-client',action='store_true',help='Explicit offline client test; no account login')
    a=p.parse_args()
    try:r=run(a.root,a.output,original_client=a.original_client)
    except (OSError,ValueError) as ex:
        print(json.dumps({'status':'blocked','error_type':type(ex).__name__,'reason':'new_safe_report_path_required'}));return 2
    print(json.dumps(r,ensure_ascii=False,indent=2));return exit_code(r,original_client=a.original_client)
if __name__=='__main__':raise SystemExit(main())
