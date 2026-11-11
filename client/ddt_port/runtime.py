"""Build our compatibility code and perform explicit, fail-closed launch checks."""
from __future__ import annotations
import ctypes.util, hashlib, io, json, os, platform, re, shutil, signal, subprocess, tempfile, time, urllib.request, urllib.parse, uuid, zipfile
from pathlib import Path
from .constants import SWT_URL,SWT_SHA512,SWT_CHECKSUM_URL
from .installer import checked_root,verify_payload,digest,managed_path
from . import patches, abi
from .lifecycle import locked, validate_timeout, supervise

PROJECT=Path(__file__).resolve().parent.parent
JAVA_ENV_KEYS=('JAVA_TOOL_OPTIONS','_JAVA_OPTIONS','JDK_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH','GDK_BACKEND','WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS','GTK_MODULES','GTK_PATH','GIO_EXTRA_MODULES','GIO_USE_VFS','GTK_IM_MODULE_FILE','LD_AUDIT','SWT_GTK3')

def clean_env(root: Path) -> dict:
    # Only desktop/session essentials; never inherit cloud, model or account tokens.
    keep={'HOME','DISPLAY','XAUTHORITY','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR',
          'LANG','LANGUAGE','LC_ALL','TZ','XDG_SESSION_TYPE'}
    env={k:v for k,v in os.environ.items() if k in keep or k.startswith('LC_')}
    for name in JAVA_ENV_KEYS:env.pop(name,None)
    oldhome=Path(env.get('HOME','/nonexistent'))
    if 'XAUTHORITY' not in env and (oldhome/'.Xauthority').is_file():env['XAUTHORITY']=str(oldhome/'.Xauthority')
    env.update({'HOME':str(root/'session/home'),'XDG_CACHE_HOME':str(root/'session/cache'),
                'XDG_CONFIG_HOME':str(root/'session/config'),'XDG_DATA_HOME':str(root/'session/data')})
    # Child processes must not inherit a Conda/user-controlled executable search path.
    env['PATH']='/usr/sbin:/usr/bin:/sbin:/bin'
    # SWT GTK's tested X11 path; never disable a browser/OS sandbox.
    env['GDK_BACKEND']='x11'
    return env

def java_version() -> dict:
    from .toolchain import selected_java
    try:
        item=selected_java()['java']
        return {'available':True,**item}
    except (ValueError,OSError,subprocess.SubprocessError) as ex:
        return {'available':False,'major':None,'error':str(ex)}


def compile_classpath(root: Path) -> list[Path]:
    rows=verify_payload(root)
    return [root/'apps/ddtclient9.jar',root/'compile-api/swt-win32.jar']+[root/x['path'] for x in rows if x['path'].startswith('libn/')]

@locked
def build(root: Path) -> dict:
    root=checked_root(root);verify_payload(root);patches.verify(root)
    from .transactions import pending
    if pending(root):raise ValueError('Recover interrupted transaction before build')
    from .toolchain import selected_java
    tools=selected_java(compiler=True)
    javac=tools['javac']['path']
    sources=sorted((PROJECT/'java').rglob('*.java'))
    if not sources:raise ValueError('Missing compatibility source code')
    with tempfile.TemporaryDirectory(prefix='.compile-',dir=root) as temp:
        command=[javac,'--release','17','-g:none','-encoding','UTF-8','-cp',os.pathsep.join(map(str,compile_classpath(root))),'-d',temp]+list(map(str,sources))
        proc=subprocess.run(command,capture_output=True,text=True,timeout=60,env=clean_env(root))
        if proc.returncode:raise ValueError('javac failed:\n'+proc.stderr[-8000:])
        output=managed_path(root,'overlay/linux-compat.jar')
        if output.is_symlink():raise ValueError('Unsafe overlay output')
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as jar:
            for file in sorted(Path(temp).rglob('*.class')):
                rel=file.relative_to(temp).as_posix()
                zi=zipfile.ZipInfo(rel,(2026,9,29,0,0,0));zi.compress_type=zipfile.ZIP_DEFLATED;zi.external_attr=0o600<<16
                jar.writestr(zi,file.read_bytes())
        output.chmod(0o600)
    result={'compiled':True,'target_java':17,'compiler':tools['javac'],
      'compile_api':'original Windows SWT; class signatures only; not a GTK runtime',
      'source_files':len(sources),'source_sha256':digest(b''.join(p.relative_to(PROJECT).as_posix().encode()+b'\0'+p.read_bytes() for p in sources)),
      'overlay_sha256':digest(output.read_bytes()),'warnings':proc.stderr.strip(),
      'native_gui_test':'not implied by compilation'}
    contract=abi.check(root)
    if not contract['passed']:raise ValueError('Compatibility ABI mismatch: '+json.dumps(contract['violations']))
    result['abi']=contract
    (root/'build-report.json').write_text(json.dumps(result,indent=2))
    try:result['runtime_sealed']=seal(root)
    except ValueError:result['runtime_sealed']=False
    return result

def _swt_jar(archive: bytes) -> bytes:
    if len(archive)>64_000_000 or hashlib.sha512(archive).hexdigest()!=SWT_SHA512:
        raise ValueError('Not the pinned official SWT 4.34 Linux x86_64 archive (SHA-512 mismatch)')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        if len(set(z.namelist()))!=len(z.namelist()):raise ValueError('Duplicate archive names')
        names=[n for n in z.namelist() if n=='swt.jar' or n.endswith('/swt.jar')]
        if len(names)!=1:raise ValueError('Expected exactly one swt.jar')
        info=z.getinfo(names[0])
        if info.file_size>30_000_000:raise ValueError('SWT JAR size limit')
        raw=z.read(info)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>120_000_000:raise ValueError('SWT expansion limit')
        if 'org/eclipse/swt/internal/gtk/OS.class' not in z.namelist():raise ValueError('Not SWT GTK')
        native=[i for i in z.infolist() if i.filename.endswith('.so')]
        if not native:raise ValueError('Missing Linux native libraries')
        for info in native:
            header=z.read(info)[:20]
            if len(header)<20 or header[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(header[18:20],'little')!=62:
                raise ValueError('Native library is not ELF64 x86_64')
    return raw

@locked
def install_swt(root: Path,archive: Path) -> dict:
    from .signed_swt import install
    return install(root,archive)


class DownloadCancelled(Exception):
    """An explicitly cancelled dependency transfer, not an accepted SDK."""


def _official_download_url(url: str) -> bool:
    parsed=urllib.parse.urlsplit(url)
    try:
        return (parsed.scheme=='https' and parsed.hostname in ('archive.eclipse.org','download.eclipse.org')
                and parsed.port in (None,443) and parsed.username is None and parsed.password is None)
    except ValueError:return False


class _HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise ValueError('Non-HTTPS redirect rejected before connection')
        if not _official_download_url(newurl):
            raise ValueError('Redirect outside approved official Eclipse hosts rejected')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download_swt(cancel=None) -> bytes:
    def cancelled():
        if cancel is not None and cancel.is_set():raise DownloadCancelled('Dependency download cancelled')
    cancelled()
    request=urllib.request.Request(SWT_URL,headers={'User-Agent':'ddt-linux-port-experimental/0.1'})
    start=time.monotonic();chunks=[];size=0
    # Default TLS verification and ordinary configured proxy support. No DNS,
    # certificate, repository, or browser-policy changes.
    with urllib.request.build_opener(_HTTPSOnlyRedirect()).open(request,timeout=15) as response:
        if not _official_download_url(response.url):raise ValueError('Unapproved dependency response URL')
        length=response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>64_000_000):
            raise ValueError('Invalid or excessive dependency Content-Length')
        while True:
            cancelled()
            if time.monotonic()-start>120:raise TimeoutError('Dependency transfer deadline exceeded')
            chunk=response.read(65536)
            if not chunk:break
            size+=len(chunk)
            if size>64_000_000:raise ValueError('Dependency download size limit')
            chunks.append(chunk)
    cancelled();blob=b''.join(chunks);_swt_jar(blob)
    return blob


@locked
def fetch_swt(root: Path, cancel=None) -> dict:
    from .signed_swt import download,install
    root=checked_root(root)
    blob=download(cancel)
    with tempfile.NamedTemporaryFile(dir=root/'native',suffix='.jar') as f:
        f.write(blob);f.flush()
        if cancel is not None and cancel.is_set():raise DownloadCancelled('Cancelled before install')
        return install(root,Path(f.name))

def download_swt(output: Path, cancel=None) -> dict:
    from .signed_swt import publish_download
    return publish_download(output,cancel)

def verify_swt(root: Path) -> None:
    from .signed_swt import verify_installed
    verify_installed(root)


def runtime_classpath(root: Path,headless: bool=False) -> list[Path]:
    rows=verify_payload(root);patches.verify(root)
    own=managed_path(root,'overlay/linux-compat.jar')
    if own.is_symlink() or not own.is_file():raise ValueError('Build compatibility code first')
    report=json.loads((root/'build-report.json').read_text())
    if digest(own.read_bytes())!=report['overlay_sha256']:raise ValueError('Compatibility overlay changed; rebuild')
    sources=sorted((PROJECT/'java').rglob('*.java'))
    expected_source=digest(b''.join(p.relative_to(PROJECT).as_posix().encode()+b'\0'+p.read_bytes() for p in sources))
    if report.get('source_sha256')!=expected_source:raise ValueError('Compatibility sources changed; rebuild this workspace using the current release')
    if not abi.check(root)['passed']:raise ValueError('Compatibility ABI mismatch; rebuild')
    swt=root/'compile-api/swt-win32.jar' if headless else root/'native/swt.jar'
    return [own,root/'patches/platform-patches.jar',swt,root/'apps/ddtclient9.jar']+[root/x['path'] for x in rows if x['path'].startswith('libn/')]

@locked
def seal(root: Path) -> bool:
    verify_swt(root);files=runtime_classpath(root)
    lines=[digest(p.read_bytes())+'  '+p.relative_to(root).as_posix() for p in files]
    out=managed_path(root,'runtime.sha256')
    if out.is_symlink():raise ValueError('Unsafe runtime lock path')
    out.write_text('\n'.join(lines)+'\n');out.chmod(0o600);return True

def doctor(root: Path | None, *, graphical_layer: str = 'browser') -> dict:
    if graphical_layer not in ('gtk','browser'):raise ValueError('Unknown graphical layer')
    issues=[];warnings=[];j=java_version()
    if platform.system()!='Linux' or platform.machine()!='x86_64':issues.append('Only Linux x86_64 is targeted by this alpha')
    if not j['available'] or j['major'] not in (17,21):issues.append('Use Linux JDK 17 or 21; other Java versions are not qualified')
    libraries={name:ctypes.util.find_library(name) for name in ('gtk-3','webkit2gtk-4.1','webkit2gtk-4.0')}
    if not libraries['gtk-3']:issues.append('Missing GTK3')
    if graphical_layer=='browser' and not (libraries['webkit2gtk-4.1'] or libraries['webkit2gtk-4.0']):issues.append('Missing WebKitGTK 4.1/4.0 shared library')
    if not os.environ.get('DISPLAY'):issues.append('No X11 DISPLAY; use a desktop terminal or xvfb-run for the local probe')
    if hasattr(os,'geteuid') and os.geteuid()==0:issues.append('Graphical tests must run as a normal user, not root; do not disable WebKit sandbox')
    if root:
        try:root=checked_root(root);verify_payload(root);patches.verify(root);verify_swt(root);runtime_classpath(root)
        except (ValueError,OSError,KeyError) as ex:issues.append(str(ex))
    else:issues.append('No prepared client root selected')
    return {'os':platform.platform(),'machine':platform.machine(),'java':j,'libraries':libraries,
        'display_configured':bool(os.environ.get('DISPLAY')),'issues':issues,'warnings':warnings,
        'graphical_layer':graphical_layer,'static_preflight_passed':not issues,'native_creation_test':'not_run_by_doctor','vendor_login_test':'not_run'}

@locked
def run_java(root: Path,main: str,args: list[str],*,headless:bool,offline:bool=True,
             timeout:float=30,cancel=None) -> dict:
    timeout=validate_timeout(timeout,allow_unbounded=main=='port.Bootstrap')
    allowed={'port.Bootstrap','port.OriginalExportProbe','port.ExportMatrixProbe',
             'port.LinkProbe','port.GuardProbe','port.NativeProbe','port.GtkProbe','port.SystemInfoProbe','port.BrowserPreflight'}
    if main not in allowed:raise ValueError('Not an allowed port entry point')
    root=checked_root(root)
    from .transactions import pending
    if pending(root):raise ValueError('Recover interrupted transaction before launching any client/probe')
    cp=runtime_classpath(root,headless=headless)
    for folder in ('session','session/home','session/tmp','session/cache','session/config','session/data','runs'):
        managed_path(root,folder)
    from .toolchain import selected_java, selected_namespace
    java=selected_java()['java']['path']
    if not headless:
        report=doctor(root,graphical_layer='gtk' if main=='port.GtkProbe' else 'browser')
        if report['issues']:raise ValueError('Native preflight blocked: '+'; '.join(report['issues']))
        seal(root)
    prefix=[]
    if offline:
        unshare=selected_namespace()
        test=subprocess.run([unshare,'--user','--map-current-user','--net','true'],capture_output=True,timeout=10,env=clean_env(root))
        if test.returncode:raise ValueError('User/network namespace unavailable; refusing offline run. Use an approved network-isolated VM instead.')
        prefix=[unshare,'--user','--map-current-user','--net']
    def execute_stage(stage,limit):
        stage_args=args if stage==main else []
        run_id=uuid.uuid4().hex
        command=[java,'-Dddt.port.run.id='+run_id,'-Xmx512m','-Dfile.encoding=UTF-8',f'-Dddt.port.root={root}',
            f'-Duser.home={root}/session/home',f'-Djava.io.tmpdir={root}/session/tmp',
            '-Dorg.eclipse.swt.browser.DefaultType=webkit','-Djava.awt.headless='+str(headless).lower(),
            '-cp',os.pathsep.join(map(str,cp)),stage,*stage_args]
        if stage == 'port.Bootstrap':
            from .exit_evidence import capture_directory, read_capture
            with capture_directory(root/'runs') as recording:
                command.insert(1, '-Dddt.port.exit.file='+str(recording))
                stage_result=supervise([*prefix,*command],cwd=root/'session',env=clean_env(root),timeout=limit,cancel=cancel)
                stage_result.update({'run_id':run_id,'offline_network_namespace':offline,'headless':headless})
                stage_result['exit_evidence']=read_capture(recording,java=java,overlay=cp[0],result=stage_result,
                    env=clean_env(root),cwd=root/'session',prefix=prefix)
                return stage_result
        stage_result=supervise([*prefix,*command],cwd=root/'session',env=clean_env(root),timeout=limit,cancel=cancel)
        stage_result.update({'run_id':run_id,'offline_network_namespace':offline,'headless':headless})
        return stage_result
    if main=='port.Bootstrap':
        from .launch_sequence import run
        result=run(execute_stage,timeout=timeout,cancel=cancel)
    else:
        result=execute_stage(main,timeout)
    result.update({'offline_network_namespace':offline,'headless':headless,
        'note':'Network namespace is not a full filesystem sandbox. Headless tests do not validate GTK or login.'})
    if main=='port.Bootstrap':
        from .startup import classify
        result['startup']=classify(result)
    return result

@locked
def probe(root: Path,kind: str,timeout:float=30,cancel=None) -> dict:
    validate_timeout(timeout)
    root=checked_root(root)
    if kind not in ('export','export-matrix','link','guard','gtk','native'):raise ValueError('Unknown probe')
    args=[];klass={'export':'port.OriginalExportProbe','export-matrix':'port.ExportMatrixProbe','link':'port.LinkProbe','guard':'port.GuardProbe','native':'port.NativeProbe','gtk':'port.GtkProbe'}[kind]
    if kind in ('export','native'):
        out=root/'runs'/('synthetic-'+kind+'-'+str(time.time_ns())+'.xls');args=[str(out)]
    if kind=='gtk':
        screenshot=root/'runs'/('synthetic-gtk-'+str(time.time_ns())+'.png');args=[str(screenshot)]
    if kind=='export-matrix':args=[str(root/'runs'/('synthetic-matrix-'+str(time.time_ns())))]
    if kind=='link':args=[str(root/'apps/ddtclient9.jar')]
    if kind=='guard' and (root/'runtime.sha256').exists():raise ValueError('Guard probe is only for an unsealed (missing native SDK) root')
    result=run_java(root,klass,args,headless=kind not in ('gtk','native'),offline=True,timeout=timeout,cancel=cancel)
    if kind in ('export','native') and out.exists():result.update({'synthetic_output':str(out),'synthetic_output_sha256':digest(out.read_bytes())})
    if kind=='gtk' and screenshot.exists():result['synthetic_screenshot']=str(screenshot)
    result['probe']=kind
    from .probe_protocol import read_measurement
    measurement,error=read_measurement(kind,result)
    if measurement is not None:result['measurement']=measurement
    if error:result['measurement_error']=error
    return result
