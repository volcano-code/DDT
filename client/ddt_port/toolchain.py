"""Verified, persisted tool selection. No shell/PATH-dependent execution or privilege changes."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib,json,os,re,stat,subprocess,tempfile
from pathlib import Path

SELECTED = ContextVar('ddt_selected_tools', default=None)
ENV_REMOVE = ('JAVA_TOOL_OPTIONS','_JAVA_OPTIONS','JDK_JAVA_OPTIONS','CLASSPATH',
 'LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','PYTHONPATH','PYTHONHOME',
 'WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS')

def clean_environment() -> dict:
    env=os.environ.copy()
    for name in ENV_REMOVE:env.pop(name,None)
    env['PATH']='/usr/sbin:/usr/bin:/sbin:/bin'
    return env

def sha(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def check_binary(path:Path, *, preserve_path:bool=False)->Path:
    if not path.is_absolute():raise ValueError('Tool path must be absolute')
    if preserve_path and (path.is_symlink() or any(x.is_symlink() for x in path.parents)):
        raise ValueError('Namespace helper must not use symbolic links')
    path=path if preserve_path else path.resolve(strict=True)
    if not path.is_file() or not os.access(path,os.X_OK):raise ValueError('Missing executable: '+str(path))
    for p in (path,*path.parents):
        s=p.stat()
        if s.st_uid!=0 or s.st_mode & (stat.S_IWGRP|stat.S_IWOTH):
            raise ValueError('Tool or parent not protected by root ownership: '+str(p))
    if path.stat().st_mode & (stat.S_ISUID|stat.S_ISGID):raise ValueError('Set-ID tool refused')
    try:
        if os.getxattr(path,'security.capability'):raise ValueError('File capabilities refused')
    except OSError as ex:
        import errno
        if ex.errno not in (errno.ENODATA,errno.ENOTSUP):raise
    return path

def version(path:Path, tool:str)->dict:
    path=check_binary(path)
    proc=subprocess.run([str(path),'-version'],env=clean_environment(),capture_output=True,text=True,timeout=10)
    text=proc.stdout+proc.stderr
    match=re.search(r'(?:javac\s+|(?:openjdk|java) version ")(\d+)(?:\.(\d+))?',text)
    if proc.returncode or not match:raise ValueError('Cannot verify '+tool+' version')
    major=int(match[1]);major=int(match[2]) if major==1 and match[2] else major
    if major not in (17,21):raise ValueError('Unsupported '+tool+' major: '+str(major))
    return {'path':str(path),'major':major,'sha256':sha(path),'description':text.splitlines()[0]}

def inspect_java(home:Path, *, compiler:bool=False)->dict:
    home=home.resolve(strict=True)
    java=version(home/'bin/java','java')
    result={'home':str(home),'java':java,'compiler_required':compiler}
    if compiler:
        javac=version(home/'bin/javac','javac')
        if javac['major']!=java['major']:raise ValueError('java/javac versions differ')
        with tempfile.TemporaryDirectory(prefix='ddt-jdk-check-') as tmp:
            p=Path(tmp)/'PortCompilerCheck.java';p.write_text('final class PortCompilerCheck { public static int value(){ return 1; } }')
            r=subprocess.run([javac['path'],'--release','17','-d',tmp,str(p)],env=clean_environment(),capture_output=True,text=True,timeout=15)
            out=Path(tmp)/'PortCompilerCheck.class'
            if r.returncode or not out.is_file() or out.read_bytes()[6:8]!=b'\x00=':
                raise ValueError('Selected javac cannot compile --release 17: '+r.stderr[-2000:])
        result['javac']=javac;result['release17_compilation_verified']=True
    return result

def discover_java(*,compiler:bool=False)->dict:
    failures=[];seen=set()
    candidates=[Path('/usr/lib/jvm/java-21-openjdk-amd64'),Path('/usr/lib/jvm/java-17-openjdk-amd64')]
    candidates+=sorted(Path('/usr/lib/jvm').glob('*'))
    for home in candidates:
        if not (home/'bin/java').exists():continue
        real=home.resolve()
        if real in seen:continue
        seen.add(real)
        try:return inspect_java(real,compiler=compiler)
        except (ValueError,OSError,subprocess.SubprocessError) as ex:failures.append(str(ex))
    raise ValueError('No complete supported '+('JDK' if compiler else 'Java runtime')+' found; '+ '; '.join(failures))

def inspect_namespace(path:Path)->dict:
    path=check_binary(path,preserve_path=True)
    parent=os.readlink('/proc/self/ns/net');uid=os.getuid()
    script='import os,json;print(json.dumps({"uid":os.getuid(),"net":os.readlink("/proc/self/ns/net")}))'
    result=subprocess.run([str(path),'--user','--map-current-user','--net','/usr/bin/python3','-I','-c',script],env=clean_environment(),capture_output=True,text=True,timeout=10)
    if result.returncode:raise ValueError('Namespace unavailable; no online fallback: '+result.stderr[-1000:])
    try:proof=json.loads(result.stdout)
    except ValueError as ex:raise ValueError('Missing namespace evidence') from ex
    if (not isinstance(proof,dict) or set(proof)!={'uid','net'}
        or type(proof.get('uid')) is not int or proof['uid']!=uid
        or not isinstance(proof.get('net'),str) or re.fullmatch(r'net:\[[0-9]+\]',proof['net']) is None
        or proof['net']==parent):raise ValueError('Namespace or UID proof mismatch')
    return {'path':str(path),'sha256':sha(path),'uid_preserved':True,'separate_net_namespace':True,
            'full_filesystem_sandbox':False,'apparmor_policy_changed':False}

def discover_namespace()->dict:
    failures=[]
    for p in (Path('/usr/local/libexec/ddt-port-ns/unshare'),Path('/usr/bin/unshare')):
        if not p.exists():continue
        try:return inspect_namespace(p)
        except (OSError,ValueError,subprocess.SubprocessError) as ex:failures.append(str(ex))
    raise ValueError('No approved working namespace helper; '+ '; '.join(failures))

def profile_path(root:Path)->Path:
    from .installer import managed_path
    return managed_path(root,'port-config.json')

def configure(root:Path,java_home:Path|None=None,unshare:Path|None=None)->dict:
    from .lifecycle import workspace_lock
    from .transactions import atomic_bytes
    with workspace_lock(root):
        java=inspect_java(java_home) if java_home else discover_java()
        ns=inspect_namespace(unshare) if unshare else discover_namespace()
        result={'schema':1,'root':str(root.absolute()),'java':java,'namespace':ns,'default_online':False}
        atomic_bytes(profile_path(root),json.dumps(result,indent=2).encode(),0o600)
        return {'status':'configured','java_major':java['java']['major'],'compiler_needed_for_normal_run':False,
                'namespace_checked':True,'system_settings_changed':False}

def load(root:Path,*,required:bool=False)->dict|None:
    p=profile_path(root)
    if not p.exists():
        if required:raise ValueError('No saved tool configuration; configure once')
        return None
    if p.is_symlink() or p.stat().st_uid!=os.geteuid() or p.stat().st_mode&0o077:raise ValueError('Unsafe profile permissions/owner')
    if p.stat().st_size>20000:raise ValueError('Profile size limit')
    data=json.loads(p.read_text())
    if data.get('schema')!=1 or data.get('root')!=str(root.absolute()) or data.get('default_online') is not False:
        raise ValueError('Unsupported profile or root mismatch')
    for row,keep in [(data['java']['java'],False),(data['namespace'],True)]:
        path=check_binary(Path(row['path']),preserve_path=keep)
        if str(path)!=row['path'] or sha(path)!=row['sha256']:
            raise ValueError('Configured tool changed; explicitly reconfigure after reviewing system update')
    if Path(data['java']['home'])/'bin/java' != Path(data['java']['java']['path']):
        raise ValueError('Java home/binary mismatch')
    return data

@contextmanager
def execution(root:Path):
    data=load(root)
    token=SELECTED.set(data)
    try:yield
    finally:SELECTED.reset(token)

def selected_java(*,compiler=False)->dict:
    cfg=SELECTED.get()
    return inspect_java(Path(cfg['java']['home']),compiler=compiler) if cfg else discover_java(compiler=compiler)

def selected_namespace()->str:
    cfg=SELECTED.get()
    if cfg:return str(check_binary(Path(cfg['namespace']['path']),preserve_path=True))
    return discover_namespace()['path']
