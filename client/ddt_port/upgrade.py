"""Unified precompiled upgrade. Plans are read-only; --apply is explicit.

Internal code-only mode cannot satisfy the GUI/RC release gate. Never touch sessions.
"""
from __future__ import annotations
import json,tempfile
from pathlib import Path
from . import installer,patches,runtime,abi,toolchain,prebuilt,transactions,signed_swt,__version__


def legacy_state(root:Path)->str:
    installer.verify_payload(root);patches.verify(root)
    overlay=installer.managed_path(root,'overlay/linux-compat.jar')
    report=installer.managed_path(root,'build-report.json')
    transactions.safe_file(overlay,optional=True);transactions.safe_file(report,optional=True)
    if not overlay.exists():
        if report.exists():raise ValueError('Build report without overlay: unsupported interrupted state')
        return 'prepared_without_overlay'
    dig=installer.digest(overlay.read_bytes())
    own,meta=prebuilt.read(runtime.PROJECT)
    candidates=json.loads((runtime.PROJECT/'ddt_port/legacy-builds.json').read_text())
    if dig==meta['overlay_sha256']:
        if report.exists() and json.loads(report.read_text()).get('source_sha256')!=meta['source_sha256']:
            raise ValueError('Build/source metadata differs from current prebuilt')
        return 'current_prebuilt' if report.exists() else 'interrupted_current_without_report'
    for row in candidates:
        if dig==row['overlay_sha256']:
            if not report.exists():return 'interrupted_'+row['state']+'_without_report'
            r=json.loads(report.read_text())
            if r.get('source_sha256')!=row['source_sha256'] or r.get('overlay_sha256')!=dig:
                raise ValueError('Legacy build metadata changed')
            return row['state']
    raise ValueError('Unknown compatibility overlay; refusing overwrite')

def swt_state(root:Path)->str:
    jar=installer.managed_path(root,'native/swt.jar')
    transactions.safe_file(jar,optional=True)
    if not jar.exists():return 'missing'
    blob=jar.read_bytes()
    if installer.digest(blob)==signed_swt.SHA256:
        signed_swt.verify_installed(root);return 'signed_p2'
    sdk=installer.managed_path(root,'native/swt-sdk.zip')
    if sdk.is_file() and not sdk.is_symlink() and sdk.stat().st_size<=64_000_000:
        if runtime._swt_jar(sdk.read_bytes())==blob:return 'legacy_bad_signature_sdk'
    raise ValueError('Unknown SWT runtime; refusing replacement')

def plan(root:Path)->dict:
    root=installer.checked_root(root)
    pending=transactions.pending(root)
    if pending:raise ValueError('Recover interrupted transaction before upgrade: '+','.join(pending))
    return {'status':'plan_ready','version':__version__,'legacy_state':legacy_state(root),'swt_state':swt_state(root),
            'compiler_invoked':False,'session_touched':False,'system_changed':False,
            'writes':['overlay/linux-compat.jar','build-report.json','runtime.sha256','upgrade-receipt.json'],
            'gui_release_gate':'blocked_until_formal_native_and_Ubuntu_tests_pass',
            'network_downloaded':False,'changes_applied':False}

def _seal_bytes(root:Path,overlay:bytes,swt:bytes)->bytes:
    rows=installer.verify_payload(root)
    paths=['overlay/linux-compat.jar','patches/platform-patches.jar','native/swt.jar','apps/ddtclient9.jar']
    paths += [r['path'] for r in rows if r['path'].startswith('libn/')]
    substitute={'overlay/linux-compat.jar':overlay,'native/swt.jar':swt}
    return ('\n'.join(installer.digest(substitute[p] if p in substitute else (root/p).read_bytes())+'  '+p for p in paths)+'\n').encode()

def apply_upgrade(root:Path,*,candidate:Path|None=None,download:bool=False,code_only:bool=False,_checkpoint=None)->dict:
    from .lifecycle import workspace_lock
    with workspace_lock(root),toolchain.execution(root):
        p=plan(root);blob,build=prebuilt.read(runtime.PROJECT)
        signed=None;receipt=None
        if candidate is not None:
            signed=signed_swt.read_candidate(candidate);signed_swt.validate(signed)
        elif download:
            signed=signed_swt.download()
        elif p['swt_state']=='signed_p2':signed=(root/'native/swt.jar').read_bytes()
        if not code_only and signed is None:
            raise ValueError('Verified signed SWT required before upgrade; unchanged. No automatic download.')
        if signed is not None:
            # Full installation always performs a real strict publisher check.
            receipt=signed_swt.verify_snapshot(root,signed,Path(toolchain.selected_java()['home']))
            sdk=root/'native/swt-sdk.zip'
            if sdk.exists():signed_swt.validate(signed,runtime._swt_jar(sdk.read_bytes()))
        build={**build,'installed_from_prebuilt':True,'runtime_sealed':signed is not None,'compiler_invoked_during_install':False}
        updates={'overlay/linux-compat.jar':blob,'build-report.json':json.dumps(build,indent=2,sort_keys=True).encode()}
        updates['runtime.sha256']=_seal_bytes(root,blob,signed) if signed is not None else b''
        if signed is not None:
            updates['native/swt.jar']=signed
            updates['native/swt-verified.json']=json.dumps({'schema':1,'version':signed_swt.BUNDLE_VERSION,
                 'sha256':signed_swt.SHA256,'source':signed_swt.URL,'jarsigner':receipt},sort_keys=True,indent=2).encode()
        updates['upgrade-receipt.json']=json.dumps({'version':__version__,'prebuilt':build['overlay_sha256'],
             'source':build['source_sha256'],'code_only':signed is None,'runtime_gui_verified':False,
             'business_login_verified':False,'session_touched':False},sort_keys=True,indent=2).encode()
        def validate():
            if not abi.check(root)['passed']:raise ValueError('Installed prebuilt ABI check failed')
            runtime.runtime_classpath(root,headless=True)
            if signed is not None:signed_swt.verify_installed(root)
            if (root/'runtime.sha256').read_bytes()!=updates['runtime.sha256']:raise ValueError('Runtime seal mismatch')
        result=transactions.apply(root,updates,validate=validate,metadata={'operation':'unified-upgrade','from':p['legacy_state']},_checkpoint=_checkpoint)
        return {**result,'version':__version__,'compiler_invoked':False,'swt_verified':signed is not None,
                'code_only':signed is None,'vendor_started':False,'gui_release_gate':'not_passed'}
