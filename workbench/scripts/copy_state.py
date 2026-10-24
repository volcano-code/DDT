#!/usr/bin/env python3
"""Make a non-destructive, permission-restricted local state copy.
Stop the source application before running. This is NOT encrypted backup or DR.
No existing destination is overwritten; credentials are never printed.
"""
import argparse,json,os,shutil,sqlite3
from pathlib import Path

def copy_state(source:Path,destination:Path):
    if source.is_symlink():raise ValueError('Source state must not be a symbolic link.')
    if destination.is_symlink():raise ValueError('Destination must not be a symbolic link.')
    source=source.resolve();destination=destination.resolve()
    if destination.exists():raise ValueError('Destination already exists; refusing to overwrite.')
    if source==destination or source in destination.parents:raise ValueError('Destination must be outside source state.')
    required=['app.sqlite3','users.json']
    for name in required:
        p=source/name
        if not p.is_file() or p.is_symlink():raise ValueError('Source must contain regular app.sqlite3 and users.json files.')
    users=json.loads((source/'users.json').read_text())
    if not isinstance(users,list) or not users:raise ValueError('Invalid source user configuration.')
    destination.mkdir(parents=True,mode=0o700);destination.chmod(0o700)
    try:
        with sqlite3.connect((source/'app.sqlite3').as_uri()+'?mode=ro',uri=True) as old:
            with sqlite3.connect(destination/'app.sqlite3') as new:
                old.backup(new)
                if new.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed.')
        for name in ['users.json','access-tokens.txt']:
            if (source/name).is_file():
                if (source/name).is_symlink():raise ValueError('Credential file must not be a symlink.')
                shutil.copyfile(source/name,destination/name)
        for file in destination.iterdir():file.chmod(0o600)
        return {'copied':True,'integrity':'ok','source_modified':False,'note':'Source app must have been stopped. Backup contains sensitive data; protect locally.'}
    except Exception:
        # Only this just-created private destination is removed on failure.
        shutil.rmtree(destination)
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path);parser.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args()
    try:print(json.dumps(copy_state(args.source,args.destination),ensure_ascii=False,indent=2))
    except (ValueError,OSError,sqlite3.Error) as exc:parser.exit(1,'State copy failed: '+str(exc)+'\n')
