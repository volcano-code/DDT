"""Durable, allowlisted runtime-file transactions; never copy or mutate session data.

Atomic per-file replacement + a durable journal, not a filesystem-wide atomic switch.
All cooperating launch/build commands must hold the existing workspace lock.
"""
from __future__ import annotations
import hashlib,json,os,stat,tempfile,uuid
from pathlib import Path
from .installer import checked_root,managed_path
from .lifecycle import workspace_lock

ALLOWED=frozenset({'overlay/linux-compat.jar','build-report.json','runtime.sha256',
                  'native/swt.jar','native/swt-verified.json','upgrade-receipt.json'})
MAX_FILE=32_000_000

def hash_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def safe_file(path:Path,*,optional:bool=False):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Symlink path refused')
    if optional and not path.exists():return
    if not path.is_file():raise ValueError('Expected regular file')
    if path.stat().st_size>MAX_FILE:raise ValueError('Transaction file size limit')

def sync_directory(path:Path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def atomic_bytes(path:Path,data:bytes,mode:int=0o600):
    safe_file(path,optional=True)
    if len(data)>MAX_FILE:raise ValueError('Transaction payload size limit')
    with tempfile.NamedTemporaryFile(prefix='.ddt-write-',dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:f.write(data);f.flush();os.fsync(f.fileno());os.chmod(tmp,mode)
        except BaseException:tmp.unlink(missing_ok=True);raise
    try:os.replace(tmp,path);sync_directory(path.parent)
    finally:tmp.unlink(missing_ok=True)

def write_journal(tx:Path,journal:dict):
    atomic_bytes(tx/'journal.json',json.dumps(journal,indent=2).encode())

def _tx_directory(root:Path)->Path:
    p=managed_path(root,'transactions')
    if not p.exists():p.mkdir(mode=0o700);sync_directory(root)
    if not p.is_dir() or p.is_symlink():raise ValueError('Unsafe transaction directory')
    return p

def _read(root:Path,txid:str):
    if not __import__('re').fullmatch(r'[0-9a-f]{32}',txid):raise ValueError('Invalid transaction ID')
    tx=managed_path(root,'transactions/'+txid);safe_file(tx/'journal.json')
    j=json.loads((tx/'journal.json').read_text())
    if j.get('schema')!=1 or j.get('id')!=txid:raise ValueError('Unsupported transaction journal')
    if j.get('status') not in ('prepared','applying','recovery_required','rolling_back','committed','rolled_back'):
        raise ValueError('Unknown transaction status; explicit inspection required')
    rows=j.get('files',[])
    if not rows or len(rows)>len(ALLOWED) or len({r.get('path') for r in rows})!=len(rows):raise ValueError('Invalid transaction rows')
    for i,row in enumerate(rows):
        if row['path'] not in ALLOWED or row['backup']!=f'{i}.before':raise ValueError('Journal path outside allowed files')
        for k in ('before','after'):
            if row[k] is not None and not __import__('re').fullmatch('[0-9a-f]{64}',row[k]):raise ValueError('Invalid digest')
        if row['before'] is not None:
            safe_file(tx/row['backup'])
            if hash_bytes((tx/row['backup']).read_bytes())!=row['before']:raise ValueError('Backup digest mismatch')
        if row['mode']&~0o777 or row['mode']&0o022:raise ValueError('Unsafe restored mode')
    return tx,j

def _current(path):
    safe_file(path,optional=True)
    return hash_bytes(path.read_bytes()) if path.exists() else None

def pending(root:Path)->list[str]:
    folder=managed_path(root,'transactions')
    if not folder.exists():return []
    values=[]
    for tx in sorted(folder.iterdir()):
        if not tx.is_dir() or tx.is_symlink():raise ValueError('Unexpected transaction entry')
        _,j=_read(root,tx.name)
        if j['status'] in ('prepared','applying','recovery_required','rolling_back'):values.append(tx.name)
    return values

def restore(root:Path,tx:Path,j:dict,*,allow_committed:bool=False,_checkpoint=None)->dict:
    if j['status']=='rolled_back':return {'status':'already_rolled_back','transaction':j['id']}
    if j['status']=='committed' and not allow_committed:raise ValueError('Use explicit rollback for committed transaction')
    if j['status'] not in ('prepared','applying','recovery_required','rolling_back','committed'):raise ValueError('Unsupported recovery state')
    # Check all targets first. Never overwrite a file changed by another actor.
    for row in j['files']:
        current=_current(managed_path(root,row['path']))
        allowed={row['after']} if j['status']=='committed' else {row['before'],row['after']}
        if current not in allowed:raise ValueError('Target changed after transaction; refusing restore: '+row['path'])
    # A committed rollback can be interrupted too. Record its direction durably
    # before restoring any file, so the next recovery accepts before/after states.
    j['status']='rolling_back';write_journal(tx,j)
    if _checkpoint:_checkpoint('before_restore',0)
    for index,row in enumerate(reversed(j['files'])):
        target=managed_path(root,row['path'])
        if row['before'] is None:target.unlink(missing_ok=True);sync_directory(target.parent)
        else:atomic_bytes(target,(tx/row['backup']).read_bytes(),row['mode'])
        if _checkpoint:_checkpoint('after_restore',index)
    j['status']='rolled_back';write_journal(tx,j)
    return {'status':'rolled_back','transaction':j['id'],'session_touched':False,'backup_retained':True}

def recover(root:Path,txid:str,*,rollback=False)->dict:
    with workspace_lock(root):
        tx,j=_read(root,txid)
        return restore(root,tx,j,allow_committed=rollback)

def apply(root:Path,updates:dict[str,bytes],*,validate=None,metadata:dict|None=None,_checkpoint=None)->dict:
    with workspace_lock(root):
        root=checked_root(root)
        if pending(root):raise ValueError('Incomplete transaction requires recovery before upgrade')
        if not updates or not set(updates)<=ALLOWED:raise ValueError('Only allowlisted runtime files can be upgraded')
        before={};modes={}
        for rel,new in updates.items():
            if not isinstance(new,bytes) or len(new)>MAX_FILE:raise ValueError('Invalid transaction payload')
            p=managed_path(root,rel);safe_file(p,optional=True)
            before[rel]=p.read_bytes() if p.exists() else None
            modes[rel]=stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600
            if modes[rel]&0o022:raise ValueError('Group/world writable target refused')
        if all(before[rel]==data for rel,data in updates.items()):
            if validate:validate()
            return {'status':'unchanged','session_touched':False}
        folder=_tx_directory(root);txid=uuid.uuid4().hex;tx=folder/txid;tx.mkdir(mode=0o700)
        rows=[]
        try:
            for i,(rel,data) in enumerate(sorted(updates.items())):
                old=before[rel]
                row={'path':rel,'backup':f'{i}.before','before':None if old is None else hash_bytes(old),
                     'after':hash_bytes(data),'mode':modes[rel]}
                if old is not None:atomic_bytes(tx/row['backup'],old,modes[rel])
                rows.append(row)
            journal={'schema':1,'id':txid,'status':'prepared','files':rows,'metadata':metadata or {}}
            write_journal(tx,journal);sync_directory(folder)
        except BaseException:
            # No active file has changed. Remove only our incomplete, uncommitted backup dir.
            __import__('shutil').rmtree(tx);sync_directory(folder);raise
        try:
            journal['status']='applying';write_journal(tx,journal)
            if _checkpoint:_checkpoint('before_commit',0)
            for i,row in enumerate(rows):
                p=managed_path(root,row['path'])
                if _current(p)!=row['before']:raise ValueError('Target changed before commit')
                atomic_bytes(p,updates[row['path']],row['mode'])
                if _checkpoint:_checkpoint('after_write',i)
            if validate:validate()
            journal['status']='committed';write_journal(tx,journal)
            return {'status':'committed','transaction':txid,'files':list(sorted(updates)),
                    'session_touched':False,'backup_retained':True,'atomicity':'per-file-with-recovery-journal'}
        except BaseException:
            try:restore(root,tx,journal)
            except Exception:
                journal['status']='recovery_required';write_journal(tx,journal)
            raise
