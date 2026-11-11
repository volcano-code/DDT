from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from . import __version__
from . import installer,patches,runtime,validation,abi,dependencies

def main(argv=None)->int:
    p=argparse.ArgumentParser(description='Experimental Linux client port; not an official or production-ready client')
    p.add_argument('--version',action='version',version=__version__)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('prepare','patch','build','doctor','fetch-swt','install-swt','probe','launch','diagnose','setup','self-test','support','gui','deps','abi','download-swt','configure','upgrade','rollback','recover'):
        q=sub.add_parser(name);q.add_argument('--root',type=Path,required=name in ('prepare',))
        if name=='configure':q.add_argument('--java-home',type=Path);q.add_argument('--unshare',type=Path)
        if name=='upgrade':
            q.add_argument('--apply',action='store_true');q.add_argument('--download',action='store_true')
            q.add_argument('--jar',type=Path);q.add_argument('--code-only',action='store_true',help='Internal milestone only; never a GUI release pass')
        if name in ('rollback','recover'):q.add_argument('--transaction',required=True)
        if name in ('setup','gui'):q.add_argument('--installer',type=Path)
        if name=='setup':q.add_argument('--archive',type=Path)
        if name=='download-swt':q.add_argument('--output',type=Path,required=True)
        if name=='deps':q.add_argument('--output',type=Path);q.add_argument('--candidates',action='store_true')
        if name=='support':q.add_argument('--output',type=Path,required=True)
        if name=='self-test':
            q.add_argument('--headless-only',action='store_true')
            q.add_argument('--timeout',type=float,default=30)
        if name=='prepare':q.add_argument('--installer',type=Path,required=True)
        if name=='install-swt':q.add_argument('--archive',type=Path,required=True,help='Pinned signed P2 .jar; atomically installs matching self-written overlay too, never a legacy SDK ZIP')
        if name=='probe':q.add_argument('--kind',choices=['export','export-matrix','link','guard','gtk','native'],required=True);q.add_argument('--timeout',type=float,default=30)
        if name=='launch':
            q.add_argument('--online',action='store_true',help='Explicitly allow normal vendor networking; only use an authorized test environment')
            q.add_argument('--acknowledge-experimental',action='store_true')
            q.add_argument('--timeout',type=float,default=60,help='Finite test run seconds; 0 means until closed/interrupt')
    args=p.parse_args(argv)
    try:
        if args.root is None and args.command not in ('deps','download-swt','gui'):
            candidate=Path.home()/'.local/share/ddt-linux-port/client-v9-test'
            if candidate.exists():args.root=candidate
            elif args.command not in ('doctor','diagnose'):raise ValueError('Choose a prepared client root once with --root')
        if args.command=='gui':
            from .desktop import launch_gui
            launch_gui(args.root,args.installer);return 0
        if args.command=='configure':
            from .toolchain import configure
            result=configure(args.root,args.java_home,args.unshare)
        elif args.command=='upgrade':
            from .upgrade import plan,apply_upgrade
            result=apply_upgrade(args.root,candidate=args.jar,download=args.download,code_only=args.code_only) if args.apply else plan(args.root)
        elif args.command in ('rollback','recover'):
            from .transactions import recover
            result=recover(args.root,args.transaction,rollback=args.command=='rollback')
        elif args.command=='download-swt':result=runtime.download_swt(args.output)
        elif args.command=='deps':result=dependencies.save_plan(args.output) if args.output else dependencies.plan(candidates=args.candidates)
        elif args.command=='abi':result=abi.check(args.root)
        elif args.command=='setup':result=validation.setup(args.root,args.installer,args.archive)
        elif args.command=='diagnose':result=validation.diagnose(args.root)
        elif args.command=='self-test':result=validation.self_test(args.root,native=not args.headless_only,timeout=args.timeout)
        elif args.command=='support':result=validation.support_bundle(args.root,args.output)
        elif args.command=='prepare':result=installer.prepare(args.installer,args.root)
        elif args.command=='patch':result=patches.apply(args.root)
        elif args.command=='build':result=runtime.build(args.root)
        elif args.command=='doctor':result=runtime.doctor(args.root)
        elif args.command=='fetch-swt':result=runtime.fetch_swt(args.root)
        elif args.command=='install-swt':result=runtime.install_swt(args.root,args.archive)
        elif args.command=='probe':result=runtime.probe(args.root,args.kind,args.timeout)
        else:
            if not args.acknowledge_experimental:raise ValueError('Original client has normal business permissions, not a read-only enforcement layer. Read safety guide and pass --acknowledge-experimental in an isolated test environment.')
            result=runtime.run_java(args.root,'port.Bootstrap',[],headless=False,offline=not args.online,timeout=args.timeout)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='launch' and result.get('startup',{}).get('classification')=='startup_error':return 2
        if args.command=='probe' and args.kind in validation.PROOFS and not validation.proof_passed(args.kind,result):return 2
        if args.command=='deps' and not result['supported_profile']:return 2
        if args.command=='abi' and not result['passed']:return 2
        if result.get('returncode',0)!=0 or result.get('timed_out') or result.get('cancelled') or result.get('output_limit_reached') or result.get('issues'):return 2
        if args.command=='diagnose' and not result['static_ready']:return 2
        if args.command=='self-test' and (not result['headless_passed'] or (not args.headless_only and not result['native_passed'])):return 2
        return 0
    except KeyboardInterrupt:return 130
    except Exception as ex:
        print(json.dumps({'status':'blocked','error_type':type(ex).__name__,'message':str(ex),'note':'No fallback to Windows installers; no fabricated success.'},ensure_ascii=False,indent=2),file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())
