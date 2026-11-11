#!/usr/bin/env python3
"""Developer/cloud build step; normal users install the emitted self-written JAR."""
import argparse,json,sys
from pathlib import Path
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from ddt_port import runtime,patches,installer,__version__
from ddt_port.prebuilt import source_digest,read
from ddt_port.transactions import atomic_bytes
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
installer.verify_payload(a.root);patches.verify(a.root)
build=runtime.build(a.root);folder=P/'artifacts';folder.mkdir(exist_ok=True)
blob=(a.root/'overlay/linux-compat.jar').read_bytes()
report={**build,'compiled_release':__version__,'client_sha256':installer.CLIENT_SHA256,
        'source_sha256':source_digest(P),'runtime_sealed':False,'gui_tested':False,
        'self_written_classes_only':True,'signature_note':'Release digest, not a publisher signing certificate'}
atomic_bytes(folder/'linux-compat.jar',blob,0o644)
atomic_bytes(folder/'prebuilt.json',json.dumps(report,indent=2).encode(),0o644)
read(P);print(json.dumps({'status':'prebuilt_created','sha256':build['overlay_sha256'],
 'source_files':build['source_files'],'abi':build['abi']['passed'],'runtime_gui_verified':False},indent=2))
