"""The reviewed, same-version signed P2 artifact. Never reinstall the bad SDK JAR."""
from __future__ import annotations
import hashlib,io,json,os,subprocess,tempfile,time,urllib.request,zipfile
from pathlib import Path
from .swt_signature_guard import validate_replacement
from .transactions import apply,atomic_bytes,safe_file

BUNDLE_VERSION='3.128.0.v20241113-2009'
NAME='org.eclipse.swt.gtk.linux.x86_64_'+BUNDLE_VERSION+'.jar'
URL='https://download.eclipse.org/eclipse/updates/4.34/R-4.34-202411201800/plugins/'+NAME
SHA256='f343ef772ba94c785beac6091cee852494b349a139d935f9fa0baf2e8a62d11b'
MAX_BYTES=30_000_000

def validate(blob:bytes,original:bytes|None=None)->dict:
    if len(blob)>MAX_BYTES or hashlib.sha256(blob).hexdigest()!=SHA256:
        raise ValueError('Not the reviewed signed SWT P2 artifact (SHA-256 mismatch); no signature stripping')
    result=validate_replacement(original if original is not None else blob,blob)
    result['trust_basis']='fixed P2 SHA-256, publisher signature metadata and all signed member digests'
    result['legacy_code_comparison']=original is not None
    return result

def strict_verify(path:Path,java_home:Path)->dict:
    from .toolchain import check_binary,clean_environment
    signer=check_binary(java_home/'bin/jarsigner')
    # This checks certificates using the selected JDK. Revocation is deliberately
    # not requested: downloads and application offline execution are separate gates.
    r=subprocess.run([str(signer),'-J-Duser.language=en','-J-Duser.country=US','-verify','-strict',str(path)],
                     env=clean_environment(),capture_output=True,text=True,timeout=30)
    out=r.stdout+r.stderr
    if r.returncode or 'jar verified.' not in out:
        raise ValueError('Strict JAR signature validation did not pass: '+out[-3000:])
    return {'passed':True,'returncode':r.returncode,'strict':True,'certificate_revocation_checked':False,
            'warnings_present':'Warning:' in out,'jar_code_executed':False}

def verify_installed(root:Path)->dict:
    jar=root/'native/swt.jar'
    if not jar.exists():raise ValueError('Missing verified signed SWT P2 runtime')
    safe_file(jar)
    original=None;sdk=root/'native/swt-sdk.zip'
    if sdk.exists():
        if sdk.is_symlink() or sdk.stat().st_size>64_000_000:raise ValueError('Unsafe original SWT SDK')
        from .runtime import _swt_jar
        original=_swt_jar(sdk.read_bytes())
    return validate(jar.read_bytes(),original)

def download(cancel=None)->bytes:
    from .runtime import _HTTPSOnlyRedirect,_official_download_url,DownloadCancelled
    def interrupted():
        if cancel is not None and cancel.is_set():raise DownloadCancelled('Dependency download cancelled')
    interrupted();chunks=[];size=0;start=time.monotonic()
    req=urllib.request.Request(URL,headers={'User-Agent':'ddt-linux-port-alpha4-dev'})
    with urllib.request.build_opener(_HTTPSOnlyRedirect()).open(req,timeout=15) as response:
        if not _official_download_url(response.url):raise ValueError('Unapproved response URL')
        length=response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>MAX_BYTES):raise ValueError('Invalid Content-Length')
        while True:
            interrupted()
            if time.monotonic()-start>120:raise TimeoutError('Download deadline exceeded')
            part=response.read(65536)
            if not part:break
            size+=len(part)
            if size>MAX_BYTES:raise ValueError('Download size limit')
            chunks.append(part)
    interrupted();blob=b''.join(chunks);validate(blob);return blob

def publish_download(output:Path,cancel=None)->dict:
    output=output.absolute();safe_file(output,optional=True)
    if output.exists():raise ValueError('Download output already exists')
    blob=download(cancel)
    with tempfile.NamedTemporaryFile(dir=output.parent,prefix='.signed-swt-') as f:
        f.write(blob);f.flush();os.fsync(f.fileno())
        os.link(f.name,output,follow_symlinks=False)
    return {'status':'downloaded_pinned_signed_candidate','output':str(output),'sha256':SHA256,'source':URL,
            'strict_certificate_check':'performed_during_install','client_started':False}

def read_candidate(path:Path)->bytes:
    """Bound the read, then bind all later validation to these immutable bytes."""
    import stat
    safe_file(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_BYTES:
            raise ValueError('Candidate must be a bounded regular JAR')
        with os.fdopen(fd,'rb',closefd=False) as f:blob=f.read(MAX_BYTES+1)
    finally:os.close(fd)
    if len(blob)>MAX_BYTES:raise ValueError('SWT size limit')
    return blob


def verify_snapshot(root:Path,blob:bytes,java_home:Path)->dict:
    """Verify exactly the bytes later installed; never verify a mutable input path.

    The temporary file is private, not a replacement for the official pinned hash.
    Its contents are compared again after jarsigner. No verification option is relaxed.
    """
    validate(blob)
    with tempfile.NamedTemporaryFile(prefix='.verify-swt-',suffix='.jar',dir=root/'native') as f:
        f.write(blob);f.flush();os.fsync(f.fileno())
        result=strict_verify(Path(f.name),java_home)
        if Path(f.name).read_bytes()!=blob:raise ValueError('Verification snapshot changed')
    return {**result,'validated_snapshot_sha256':hashlib.sha256(blob).hexdigest()}


def install(root:Path,path:Path)->dict:
    """Install through the same transaction as a compatibility-layer upgrade.

    In alpha.4-dev this function required the old overlay to match the NEW source
    before it could install SWT, blocking alpha.3 upgrades with --archive. Do not
    construct a live classpath until the whole staged version has been committed.
    """
    from .installer import checked_root
    from .lifecycle import workspace_lock
    from .upgrade import apply_upgrade
    with workspace_lock(root):
        root=checked_root(root)
        blob=read_candidate(path)
        try:validate(blob)
        except ValueError as ex:
            raise ValueError('Install only the signed P2 .jar; legacy SDK ZIP cannot overwrite the repaired SWT. '+str(ex)) from ex
        # Immutable validated snapshot; apply_upgrade independently revalidates it.
        with tempfile.NamedTemporaryFile(dir=root/'native',suffix='.jar') as f:
            f.write(blob);f.flush()
            result=apply_upgrade(root,candidate=Path(f.name))
        return {**result,'operation':'signed-swt-and-compatible-overlay','vendor_started':False}
