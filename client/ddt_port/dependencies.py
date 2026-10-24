"""Distribution-specific dependency guidance. Never executes apt/sudo or changes sources."""
from __future__ import annotations
import json, os, platform, shlex, shutil, subprocess
from pathlib import Path

PROFILES = {
    ('debian','12'): {'jdk':'openjdk-17-jdk','gtk':'libgtk-3-0'},
    ('debian','13'): {'jdk':'openjdk-21-jdk','gtk':'libgtk-3-0t64'},
    ('ubuntu','24.04'): {'jdk':'openjdk-21-jdk','gtk':'libgtk-3-0t64'},
}

def os_release(path: Path = Path('/etc/os-release')) -> dict:
    data = path.read_text(encoding='utf-8')
    if len(data) > 65536: raise ValueError('Oversized os-release')
    found = {}
    for line in data.splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        key, value = line.split('=',1)
        if key not in ('ID','VERSION_ID'): continue
        if key in found: raise ValueError('Duplicate os-release key')
        tokens = shlex.split(value, comments=False)
        if len(tokens) != 1: raise ValueError('Invalid os-release field')
        found[key] = tokens[0]
    return found

def plan(*, release: dict | None = None, machine: str | None = None, candidates: bool = False) -> dict:
    release = os_release() if release is None else release
    machine = platform.machine() if machine is None else machine
    profile = PROFILES.get((release.get('ID'),release.get('VERSION_ID')))
    result = {'schema':1,'distribution':release.get('ID','unknown'), 'release':release.get('VERSION_ID','unknown'),
              'machine':machine, 'supported_profile':profile is not None and machine=='x86_64',
              'packages':[], 'commands':[], 'commands_executed':False, 'system_changed':False,
              'native_runtime_verified':False,
              'note':'Package names are a provisioning profile, not proof this distribution has passed client tests.'}
    if not result['supported_profile']:
        result['status']='blocked';result['reason']='Only Debian 12/13 and Ubuntu 24.04 on x86_64 have explicit profiles; never mix distributions.'
        return result
    packages = ['python3','python3-tk',profile['jdk'],profile['gtk'],'libwebkit2gtk-4.1-0','util-linux','ca-certificates']
    result.update({'status':'plan_ready','packages':packages,
                   'commands':[['sudo','apt-get','update'],['sudo','apt-get','install','--no-remove','--no-install-recommends',*packages]],
                   'optional_probe_packages':['xvfb','xauth','dbus-x11'],
                   'swt_required_separately':True})
    result['shell_preview'] = [' '.join(shlex.quote(x) for x in command) for command in result['commands']]
    if candidates:
        if not shutil.which('apt-cache'):
            result['candidate_check']={'status':'blocked','reason':'apt-cache missing'}
        else:
            proc = subprocess.run(['apt-cache','policy',*packages],capture_output=True,text=True,timeout=15,
                                  env={**os.environ,'LC_ALL':'C'})
            # Local apt-cache only. Never runs update, apt install, or contacts a repository.
            result['candidate_check']={'status':'checked' if proc.returncode==0 else 'failed',
               'returncode':proc.returncode, 'output':proc.stdout[:24000],
               'note':'An empty candidate list can mean missing/stale local package indexes, not a nonexistent package.'}
    return result

def save_plan(path: Path) -> dict:
    result = plan(candidates=True)
    path = path.absolute()
    for parent in (path.parent,*path.parent.parents):
        if parent.is_symlink(): raise ValueError('Plan destination includes symlink')
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    return result
