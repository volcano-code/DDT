#!/usr/bin/env python3
"""Build a launcher-only Debian package. No maintainer scripts or proprietary payload.

Building never installs anything. APT resolves authenticated system dependencies
only when the user separately installs the resulting package on their machine.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from ddt_port import __version__
from ddt_port import prebuilt

ALLOWED_EXTENSIONS={'.py','.java','.json','.md','.sh','.desktop',''}
DEB_VERSION=__version__.replace('-alpha.','~alpha.')

def build(output: Path) -> dict:
    if not shutil.which('dpkg-deb'):raise ValueError('dpkg-deb is required to build a package, not to run the ZIP distribution')
    output=output.absolute()
    if output.exists() or output.is_symlink():raise ValueError('Package output exists')
    for parent in (output.parent,*output.parent.parents):
        if parent.is_symlink():raise ValueError('Package destination includes symlink')
    with tempfile.TemporaryDirectory(prefix='ddt-deb-build-') as tmp:
        staging=Path(tmp)/'package';control=staging/'DEBIAN';control.mkdir(parents=True)
        home=staging/'usr/share/ddt-linux-port';home.mkdir(parents=True)
        selected=[]
        for directory in ('ddt_port','java','bin'):
            for source in sorted((PROJECT/directory).rglob('*')):
                if source.is_symlink():raise ValueError('Symlink in package source')
                if not source.is_file() or '__pycache__' in source.parts:continue
                if source.suffix not in ALLOWED_EXTENSIONS:raise ValueError('Unexpected package input: '+source.name)
                selected.append(source)
        prebuilt.read(PROJECT)
        selected += [PROJECT/'artifacts/linux-compat.jar',PROJECT/'artifacts/prebuilt.json']
        selected += [PROJECT/'launch.sh',PROJECT/'NOTICE.md',PROJECT/'README.md']
        selected += list((PROJECT/'docs').glob('*.md'))
        for source in selected:
            target=home/source.relative_to(PROJECT);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(source.read_bytes());target.chmod(0o644)
        executable=staging/'usr/bin/ddt-linux-port';executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nset -eu\nif [ "$#" -eq 0 ]; then\n  exec /usr/bin/python3 /usr/share/ddt-linux-port/bin/ddt-linux gui\nfi\nexec /usr/bin/python3 /usr/share/ddt-linux-port/bin/ddt-linux "$@"\n')
        executable.chmod(0o755)
        desktop=staging/'usr/share/applications/ddt-linux-port.desktop';desktop.parent.mkdir(parents=True)
        desktop.write_bytes((PROJECT/'packaging/ddt-linux-port.desktop').read_bytes());desktop.chmod(0o644)
        documentation=staging/'usr/share/doc/ddt-linux-port';documentation.mkdir(parents=True)
        (documentation/'copyright').write_text('This package contains project-written port tools only.\n'
            'See /usr/share/ddt-linux-port/NOTICE.md for the experimental scope.\n'
            'No original commercial software, vendor-derived class overlays, SDK, fonts, or customer data are included.\n')
        (control/'control').write_text(f'''Package: ddt-linux-port
Version: {DEB_VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Local DDT Port Project <ddt-port@localhost.invalid>
Depends: python3 (>= 3.10), python3-tk, openjdk-21-jdk | openjdk-17-jdk, libgtk-3-0t64 | libgtk-3-0, libwebkit2gtk-4.1-0, util-linux, ca-certificates
Recommends: xauth, xvfb, dbus-x11
Description: Experimental Linux launcher for a locally supplied DDT client
 Deployment and diagnostics only. Requires the separately supplied original
 installer and a reviewed same-version signed Eclipse SWT P2 runtime.
 Does not contain the commercial client and is not a verified production port.
''')
        records=[]
        for path in sorted(staging.rglob('*')):
            if path.is_dir():path.chmod(0o755)
            elif path.is_file():
                if path!=executable:path.chmod(0o644)
                os.utime(path,(1790899200,1790899200))
                if not path.is_relative_to(control):records.append(hashlib.md5(path.read_bytes()).hexdigest()+'  '+path.relative_to(staging).as_posix())
        (control/'md5sums').write_text('\n'.join(records)+'\n')
        built=Path(tmp)/'output.deb'
        command=['dpkg-deb','--build','--root-owner-group','-Zxz',str(staging),str(built)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=60,env={**os.environ,'SOURCE_DATE_EPOCH':'1790899200'})
        if result.returncode:raise ValueError('dpkg-deb failed: '+result.stderr[-2000:])
        data=built.read_bytes()
        with os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644),'wb') as handle:handle.write(data)
    return {'package':str(output),'version':DEB_VERSION,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'proprietary_payload_included':False,'maintainer_scripts':False,'package_installed':False,
            'dependency_installation_tested':False}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();print(json.dumps(build(args.output),indent=2))
if __name__=='__main__':main()
