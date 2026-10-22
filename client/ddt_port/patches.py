"""Narrow, reproducible constant-pool overlay. No method/branch editing."""
from __future__ import annotations
import io, json, struct, zipfile
from pathlib import Path
from .lifecycle import locked
from .installer import checked_root, verify_payload, digest, managed_path

def split_class(raw: bytes) -> tuple[list[tuple[int,bytes] | None],bytes]:
    if len(raw)<10 or raw[:4]!=b'\xca\xfe\xba\xbe': raise ValueError('Invalid class header')
    count=struct.unpack_from('>H',raw,8)[0]; entries=[None]; pos=10
    sizes={3:4,4:4,5:8,6:8,7:2,8:2,9:4,10:4,11:4,12:4,15:3,16:2,17:4,18:4,19:2,20:2}
    while len(entries)<count:
        if pos>=len(raw): raise ValueError('Truncated constant pool')
        tag=raw[pos];pos+=1
        if tag==1:
            if pos+2>len(raw): raise ValueError('Truncated UTF8 length')
            size=2+struct.unpack_from('>H',raw,pos)[0]
        elif tag in sizes: size=sizes[tag]
        else: raise ValueError('Unsupported constant tag')
        if pos+size>len(raw): raise ValueError('Truncated constant')
        entries.append((tag,raw[pos:pos+size]));pos+=size
        if tag in (5,6): entries.append(None)
    if len(entries)!=count or len(raw)-pos<6: raise ValueError('Invalid class tail')
    return entries,raw[pos:]

def patch_class(raw: bytes, spec: dict) -> tuple[bytes,dict]:
    if digest(raw)!=spec['sha256']: raise ValueError('Unsupported class hash: '+spec['class'])
    entries,tail=split_class(raw); changed=[]
    if 'integer' in spec:
        r=spec['integer'];index=r['index']
        if entries[index]!=(3,struct.pack('>i',r['old'])): raise ValueError('Browser constant mismatch')
        entries[index]=(3,struct.pack('>i',r['new']));changed.append(index)
    for r in spec.get('utf8',[]):
        old=r['old'].encode('utf-8');new=r['new'].encode('utf-8')
        # Only ASCII/control concat recipes: no lossy decoding of Java modified UTF-8.
        target=(1,struct.pack('>H',len(old))+old)
        indices=[i for i,e in enumerate(entries) if e==target]
        if len(indices)!=1: raise ValueError('Path recipe count mismatch')
        i=indices[0];entries[i]=(1,struct.pack('>H',len(new))+new);changed.append(i)
    if not changed: raise ValueError('Empty patch')
    out=raw[:10]+b''.join(bytes([e[0]])+e[1] for e in entries if e is not None)+tail
    _,newtail=split_class(out)
    if newtail!=tail: raise AssertionError('Method/attribute bytes changed')
    return out,{'class':spec['class'],'before_sha256':digest(raw),'after_sha256':digest(out),
        'changed_pool_entries':changed,'method_attribute_tail_identical':True}

def expected_patch(original: bytes) -> tuple[bytes,list[dict]]:
    spec=json.loads((Path(__file__).with_name('patch-spec.json')).read_text())
    if digest(original)!=spec['client_sha256']: raise ValueError('Unsupported client JAR')
    source=zipfile.ZipFile(io.BytesIO(original)); target=io.BytesIO(); report=[]
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for entry in spec['classes']:
            data,row=patch_class(source.read(entry['class']),entry)
            zi=zipfile.ZipInfo(entry['class'],date_time=(2026,9,29,0,0,0));zi.compress_type=zipfile.ZIP_DEFLATED
            zi.external_attr=0o600<<16;out.writestr(zi,data);report.append(row)
    return target.getvalue(),report

@locked
def apply(root: Path) -> dict:
    root=checked_root(root);verify_payload(root)
    blob,rows=expected_patch((root/'apps/ddtclient9.jar').read_bytes())
    out=managed_path(root,'patches/platform-patches.jar')
    if out.is_symlink(): raise ValueError('Unsafe patch path')
    out.write_bytes(blob);out.chmod(0o600)
    result={'patched_classes':len(rows),'browser_constructor_sites':11,'path_recipes':4,
        'original_client_unchanged':True,'patch_sha256':digest(blob),'classes':rows,
        'warning':'Generated locally from your original program. Do not redistribute vendor-derived overlay.'}
    (root/'patch-report.json').write_text(json.dumps(result,indent=2))
    return result

def verify(root: Path) -> None:
    expected,_=expected_patch((root/'apps/ddtclient9.jar').read_bytes())
    target=root/'patches/platform-patches.jar'
    if target.is_symlink() or not target.is_file() or target.read_bytes()!=expected:
        raise ValueError('Missing or modified platform overlay; rerun patch')
