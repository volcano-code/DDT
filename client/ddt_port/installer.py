"""Extract ONLY the locally supplied, hash-pinned Java payload. Never execute NSIS."""
from __future__ import annotations
import hashlib, io, json, lzma, os, shutil, struct, tempfile, zipfile, zlib
from pathlib import Path, PurePosixPath
from .constants import INSTALLER_SHA256, INSTALLER_BYTES, CLIENT_SHA256, MAX_BLOCK, ROOT_MARKER

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def u32(data: bytes, pos: int) -> int:
    if pos < 0 or pos + 4 > len(data):
        raise ValueError('Truncated integer')
    return struct.unpack_from('<I', data, pos)[0]

def block(data: bytes, pos: int) -> tuple[bytes, int]:
    flags = u32(data, pos); length = flags & 0x7fffffff
    if pos + 4 + length > len(data):
        raise ValueError('Block outside archive')
    raw = data[pos + 4:pos + 4 + length]
    if not flags & 0x80000000:
        if length > MAX_BLOCK: raise ValueError('Block expansion limit')
        return raw, pos + 4 + length
    if len(raw) < 5: raise ValueError('Missing LZMA properties')
    prop, dictionary = raw[0], u32(raw, 1)
    if prop >= 225 or dictionary > 2**27: raise ValueError('Unsafe LZMA parameters')
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
        'id': lzma.FILTER_LZMA1, 'dict_size': dictionary, 'lc': prop % 9,
        'lp': prop // 9 % 5, 'pb': prop // 45}])
    result = decoder.decompress(raw[5:], max_length=MAX_BLOCK + 1)
    if len(result) > MAX_BLOCK or not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid or overlarge compressed block')
    return result, pos + 4 + length

def safe_relative(name: str) -> Path:
    # Archive paths are labels, not trusted filesystem paths.
    if not name or '\\' in name or '\x00' in name or ':' in name:
        raise ValueError('Unsafe relative path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('..', '.', '') for x in name.split('/')):
        raise ValueError('Path traversal or ambiguous path')
    return Path(*p.parts)

def inventory(path: Path) -> tuple[bytes, list[dict]]:
    if path.stat().st_size != INSTALLER_BYTES: raise ValueError('Unsupported installer size')
    raw = path.read_bytes()
    if digest(raw) != INSTALLER_SHA256: raise ValueError('Unsupported installer SHA-256; refusing another version')
    if zlib.crc32(raw[512:-4]) != u32(raw, len(raw)-4): raise ValueError('NSIS CRC mismatch')
    start = raw.index(b'\xef\xbe\xad\xdeNullsoftInst') - 4
    if start + u32(raw, start+24) != len(raw): raise ValueError('NSIS size mismatch')
    header, data_start = block(raw, start+28)
    if len(header) != u32(raw, start+20): raise ValueError('NSIS header size mismatch')
    entry_start, count = u32(header, 20), u32(header, 24)
    string_start, string_end = u32(header, 28), u32(header, 36)
    if entry_start + count*28 > len(header) or not 0 <= string_start <= string_end <= len(header):
        raise ValueError('Invalid NSIS tables')
    def text(offset: int) -> str:
        if not 0 <= offset < string_end-string_start: raise ValueError('Invalid string offset')
        end = header.find(b'\0', string_start+offset, string_end)
        if end < 0: raise ValueError('Unterminated string')
        value = header[string_start+offset:end]
        if value.startswith(b'\xfd') and len(value) >= 3: value = b'ROOT'+value[3:]
        return value.decode('gb18030', 'strict').replace('\\', '/')
    starts = set(); p = data_start
    while p < len(raw)-4:
        starts.add(p); p += 4 + (u32(raw,p) & 0x7fffffff)
    if p != len(raw)-4: raise ValueError('NSIS block alignment failure')
    rows=[]; folder='ROOT'
    for i in range(count):
        e=struct.unpack_from('<7i',header,entry_start+i*28)
        if e[0]==11 and e[2]==1: folder=text(e[1]).strip('/')
        if e[0]!=20: continue
        label=folder+'/'+text(e[2]); pos=data_start+e[3]
        if pos not in starts: raise ValueError('Invalid file block reference')
        rows.append({'label':label,'offset':pos,'instruction':i})
    return raw, rows

def check_jar(raw: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(raw)) as jar:
        if sum(i.file_size for i in jar.infolist()) > 150_000_000: raise ValueError('JAR expansion limit')
        if len(jar.namelist()) != len(set(jar.namelist())): raise ValueError('Duplicate JAR members')
        if jar.testzip(): raise ValueError('JAR CRC mismatch')

def prepare(installer: Path, destination: Path) -> dict:
    destination=destination.absolute()
    if destination.exists() or destination.is_symlink(): raise ValueError('Output already exists; use a new directory')
    raw, rows=inventory(installer)
    chosen={}
    for row in rows:
        label=row['label']
        if label=='ROOT/apps/ddtclient9.jar' or (label.startswith('ROOT/libn/') and label.endswith('.jar')):
            rel=label.removeprefix('ROOT/'); safe_relative(rel)
            if rel in chosen and chosen[rel]['offset'] != row['offset']:
                raise ValueError('Ambiguous NSIS branch payload')
            chosen[rel]=row
    if 'apps/ddtclient9.jar' not in chosen: raise ValueError('Missing client')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.ddt-preparing-',dir=destination.parent))
    try:
        records=[]
        for rel,row in sorted(chosen.items()):
            contents,_=block(raw,row['offset']); check_jar(contents)
            dest_rel = 'compile-api/swt-win32.jar' if 'org.eclipse.swt.win32' in rel else rel
            target=temp/safe_relative(dest_rel);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(contents);target.chmod(0o600)
            records.append({'path':dest_rel,'installer_path':rel,'sha256':digest(contents),'bytes':len(contents),
                'purpose':'compile-only; never graphical runtime' if dest_rel.startswith('compile-api/') else 'vendor-payload'})
        if digest((temp/'apps/ddtclient9.jar').read_bytes())!=CLIENT_SHA256: raise ValueError('Client SHA mismatch')
        for name in ['native','overlay','patches','runs','session/home','session/cache','session/data','session/config','session/tmp']:
            (temp/name).mkdir(parents=True,exist_ok=True);(temp/name).chmod(0o700)
        manifest={'format':1,'installer_sha256':INSTALLER_SHA256,'files':records,
            'note':'Locally extracted user-supplied payload. Windows SWT is compile-only. No EXE/DLL/JRE extracted.'}
        (temp/'payload-lock.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (temp/ROOT_MARKER).write_text(INSTALLER_SHA256+'\n')
        os.rename(temp,destination)
        return {'root':str(destination),'files':len(records),'client_sha256':CLIENT_SHA256,'executed_installer':False}
    except BaseException:
        shutil.rmtree(temp,ignore_errors=True);raise

def checked_root(root: Path) -> Path:
    root=root.absolute()
    if root.is_symlink() or (root/ROOT_MARKER).is_symlink() or not (root/ROOT_MARKER).is_file(): raise ValueError('Not a prepared port root')
    if (root/ROOT_MARKER).read_text().strip()!=INSTALLER_SHA256: raise ValueError('Unsupported port root')
    return root

def verify_payload(root: Path) -> list[dict]:
    root=checked_root(root); rows=json.loads((root/'payload-lock.json').read_text())['files']
    if not rows or len({x['path'] for x in rows})!=len(rows): raise ValueError('Invalid payload manifest')
    expected=json.loads(Path(__file__).with_name('payload-spec.json').read_text())
    compare=lambda rs: sorted((x['path'],x['sha256'],x['bytes']) for x in rs)
    if compare(rows)!=compare(expected): raise ValueError('Payload manifest differs from pinned installer')
    allowed={x['path'] for x in expected if x['path'].startswith('libn/')}
    if {p.relative_to(root).as_posix() for p in (root/'libn').glob('*.jar')}!=allowed:raise ValueError('Unexpected runtime JAR in libn')
    for item in rows:
        p=root/safe_relative(item['path'])
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root.resolve()): raise ValueError('Missing/unsafe payload')
        if digest(p.read_bytes())!=item['sha256']: raise ValueError('Payload hash mismatch: '+item['path'])
    if digest((root/'apps/ddtclient9.jar').read_bytes())!=CLIENT_SHA256: raise ValueError('Unsupported client')
    return rows


def managed_path(root: Path, relative: str) -> Path:
    """Only paths inside the prepared workspace; reject symlinked parent directories."""
    root=checked_root(root); rel=safe_relative(relative); result=root/rel
    candidate=result
    while candidate!=root:
        if candidate.is_symlink(): raise ValueError('Symlink in managed output path')
        candidate=candidate.parent
    if not result.resolve().is_relative_to(root.resolve()):raise ValueError('Managed path leaves workspace')
    return result
