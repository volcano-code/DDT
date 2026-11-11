"""Check replacement-class binary contracts without executing vendor bytecode.

This checks member name/descriptor/access, not behavior, authentication, or GTK.
It deliberately inspects only the five explicitly reviewed replacement families.
"""
from __future__ import annotations
import struct
import zipfile
from pathlib import Path
from .patches import split_class
from .installer import checked_root, verify_payload, digest

SHIMS = (
    'com/sd/ddt/util/JavaEnvUtil',
    'com/sd/ddt/util/DependencyChecker',
    'com/sd/common/util/device/WebView2Detector',
    'com/sd/common/util/swt/browser/BrowserSetup',
    'com/sd/common/util/device/SystemInfoUtil',
)

class Reader:
    def __init__(self, data: bytes): self.data, self.pos = data, 0
    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data): raise ValueError('Truncated class member table')
        out = self.data[self.pos:self.pos+n]; self.pos += n; return out
    def u2(self) -> int: return struct.unpack('>H', self.read(2))[0]
    def u4(self) -> int: return struct.unpack('>I', self.read(4))[0]

def inspect_class(raw: bytes) -> dict:
    if len(raw) > 8_000_000: raise ValueError('Class size limit')
    pool, tail = split_class(raw)
    def entry(index: int, tag: int):
        if not 0 < index < len(pool) or not pool[index] or pool[index][0] != tag:
            raise ValueError('Invalid class constant reference')
        return pool[index][1]
    def text(index: int):
        value = entry(index, 1)
        # Names and descriptors here are ASCII. Unrelated vendor strings may be
        # modified UTF-8 and are never decoded by this contract checker.
        try: return value[2:].decode('utf-8')
        except UnicodeDecodeError as ex: raise ValueError('Unsupported member identifier encoding') from ex
    def klass(index: int): return text(struct.unpack('>H', entry(index, 7))[0])
    r = Reader(tail); flags = r.u2(); name = klass(r.u2()); super_index = r.u2()
    interfaces = [klass(r.u2()) for _ in range(r.u2())]
    members = []
    for kind in ('field', 'method'):
        for _ in range(r.u2()):
            access, member, descriptor = r.u2(), text(r.u2()), text(r.u2())
            members.append({'kind':kind, 'name':member, 'descriptor':descriptor, 'access':access})
            for _ in range(r.u2()): r.u2(); r.read(r.u4())
    for _ in range(r.u2()): r.u2(); r.read(r.u4())
    if r.pos != len(tail): raise ValueError('Unexpected trailing class bytes')
    references = []
    for item in pool:
        if not item or item[0] not in (9, 10, 11): continue
        ci, ni = struct.unpack('>HH', item[1]); owner = klass(ci)
        if owner not in SHIMS: continue
        name_i, desc_i = struct.unpack('>HH', entry(ni, 12))
        references.append({'owner': owner, 'kind': 'field' if item[0] == 9 else 'method',
                           'name': text(name_i), 'descriptor': text(desc_i)})
    return {'name':name, 'access':flags, 'super':klass(super_index) if super_index else None,
            'interfaces':interfaces, 'members':members, 'references':references}

def compare(original: dict, replacement: dict) -> list[str]:
    """Require public/protected ABI and superclass/interface identity to be retained."""
    failures = []
    if original['name'] != replacement['name']: failures.append('class-name')
    if original['super'] != replacement['super']: failures.append('superclass')
    if original['interfaces'] != replacement['interfaces']: failures.append('interfaces')
    if original['access'] & 1 and not replacement['access'] & 1: failures.append('class-access')
    actual = {(x['kind'],x['name'],x['descriptor']):x for x in replacement['members']}
    for member in original['members']:
        if not member['access'] & (1|4): continue
        key = member['kind'],member['name'],member['descriptor']; other = actual.get(key)
        label = member['name'] + member['descriptor']
        if other is None: failures.append('missing:' + label); continue
        if bool(member['access'] & 8) != bool(other['access'] & 8): failures.append('staticness:' + label)
        if member['access'] & 1 and not other['access'] & 1: failures.append('public-access:' + label)
        if member['access'] & 4 and not other['access'] & (1|4): failures.append('protected-access:' + label)
        if not member['access'] & 0x400 and other['access'] & 0x400: failures.append('became-abstract:' + label)
        if not member['access'] & 0x10 and other['access'] & 0x10: failures.append('became-final:' + label)
    return failures

def check(root: Path) -> dict:
    root = checked_root(root); verify_payload(root)
    overlay = root/'overlay/linux-compat.jar'
    if overlay.is_symlink() or not overlay.is_file(): raise ValueError('Build compatibility code before ABI check')
    violations = []; rows = []; references = []
    with zipfile.ZipFile(root/'apps/ddtclient9.jar') as vendor, zipfile.ZipFile(overlay) as own:
        originals, replacements = {}, {}
        for name in SHIMS:
            originals[name] = inspect_class(vendor.read(name+'.class'))
            replacements[name] = inspect_class(own.read(name+'.class'))
            errors = compare(originals[name], replacements[name])
            violations.extend({'owner':name,'issue':x} for x in errors)
            rows.append({'class':name, 'public_protected_members':sum(bool(x['access'] & (1|4)) for x in originals[name]['members']),
                         'passed':not errors})
        classes = [name for name in vendor.namelist() if name.endswith('.class')]
        for name in classes:
            parsed = inspect_class(vendor.read(name))
            # Original bodies of replaced classes are not active on this classpath.
            if parsed['name'] in SHIMS: continue
            for ref in parsed['references']:
                references.append({'caller':name, **ref})
                wanted = (ref['kind'],ref['name'],ref['descriptor'])
                actual = {(x['kind'],x['name'],x['descriptor']) for x in replacements[ref['owner']]['members']}
                if wanted not in actual: violations.append({'owner':ref['owner'],'issue':'unresolved-reference','caller':name,'member':ref['name']})
    return {'schema':1, 'kind':'replacement-abi-static', 'passed':not violations,
            'client_sha256':digest((root/'apps/ddtclient9.jar').read_bytes()), 'overlay_sha256':digest(overlay.read_bytes()),
            'classes_scanned':len(classes), 'active_original_classes':len(classes)-len(SHIMS), 'replacement_classes':len(rows),
            'public_protected_members':sum(x['public_protected_members'] for x in rows),
            'reference_sites':len(references), 'classes':rows, 'violations':violations,
            'vendor_code_executed':False, 'native_gui_tested':False,
            'limitation':'ABI compatibility is not method behavior, native rendering, or login acceptance.'}
