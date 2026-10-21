"""Download one public Eclipse dependency. Never accepts a URL or any credential."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request

URL = 'https://download.eclipse.org/eclipse/updates/4.34/R-4.34-202411201800/plugins/org.eclipse.swt.gtk.linux.x86_64_3.128.0.v20241113-2009.jar'
SHA256 = 'f343ef772ba94c785beac6091cee852494b349a139d935f9fa0baf2e8a62d11b'
MAX_BYTES = 30_000_000

def allowed(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return (u.scheme == 'https' and u.hostname in {'download.eclipse.org', 'archive.eclipse.org'}
            and u.port in (None, 443) and not u.username and not u.password)

class StrictRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('Unapproved public dependency redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(destination: Path, jdk: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Dependency destination already exists')
    opener = urllib.request.build_opener(StrictRedirect())
    with opener.open(URL, timeout=25) as response:
        if not allowed(response.url):
            raise ValueError('Unapproved response URL')
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Official SWT digest mismatch; no bypass')
    with destination.open('xb') as stream:
        stream.write(data)
    os.chmod(destination, 0o600)
    try:
        env = {'PATH': str(jdk / 'bin') + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'HOME': os.environ['HOME']}
        result = subprocess.run([str(jdk / 'bin/jarsigner'), '-J-Duser.language=en',
            '-J-Duser.country=US', '-verify', '-strict', str(destination)],
            env=env, capture_output=True, text=True, timeout=40)
        if result.returncode != 0 or 'jar verified.' not in result.stdout + result.stderr:
            raise ValueError('Strict signature validation failed; no bypass')
        print('OFFICIAL_SWT_DIGEST_AND_STRICT_SIGNATURE_PASS; revocation_not_checked')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: fetch_public_swt.py NEW_JAR JDK_HOME')
    fetch(Path(sys.argv[1]), Path(sys.argv[2]))
