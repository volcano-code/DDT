"""Install only our hash-pinned, source-bound compiled adapter; never vendor bytecode."""
from __future__ import annotations
import json,struct,zipfile
from pathlib import Path
from .installer import digest,CLIENT_SHA256
from .transactions import safe_file

ALLOWED=('port/','com/sd/ddt/util/JavaEnvUtil','com/sd/ddt/util/DependencyChecker',
         'com/sd/common/util/device/WebView2Detector','com/sd/common/util/swt/browser/BrowserSetup',
         'com/sd/common/util/device/SystemInfoUtil')

def source_digest(project:Path)->str:
    files=sorted((project/'java').rglob('*.java'))
    if not files:raise ValueError('Missing self-written source files')
    return digest(b''.join(p.relative_to(project).as_posix().encode()+b'\0'+p.read_bytes() for p in files))

def read(project:Path)->tuple[bytes,dict]:
    path=project/'artifacts/linux-compat.jar';meta=project/'artifacts/prebuilt.json'
    safe_file(path);safe_file(meta)
    report=json.loads(meta.read_text());blob=path.read_bytes()
    if (report.get('client_sha256')!=CLIENT_SHA256 or report.get('overlay_sha256')!=digest(blob)
            or report.get('source_sha256')!=source_digest(project)):
        raise ValueError('Prebuilt identity/source/client mismatch')
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or not names:raise ValueError('Invalid prebuilt entries')
        for name in names:
            raw=z.read(name)
            if (not name.endswith('.class') or not name.startswith(ALLOWED) or '..' in name.split('/')
                or raw[:4]!=b'\xca\xfe\xba\xbe' or struct.unpack('>H',raw[6:8])[0]!=61):
                raise ValueError('Unexpected compiled adapter member: '+name)
    return blob,report
