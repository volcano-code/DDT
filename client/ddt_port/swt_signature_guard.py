"""SWT alpha.3 hotfix: preserve valid publisher signatures; never strip them.

The original, SHA-512 pinned SDK remains the trust anchor. Replacement jars
must have the very same signature metadata, all executable bytes, and a
manifest that satisfies the anchored signature-file digest. This is not a
certificate revocation service or a generic JAR verifier.
"""
from __future__ import annotations
import base64
import hashlib
import io
import re
import stat
import zipfile

MAX_JAR = 30_000_000
MAX_EXPANDED = 120_000_000
BUNDLE_ID = 'org.eclipse.swt.gtk.linux.x86_64'
BUNDLE_VERSION = '3.128.0.v20241113-2009'


def _members(blob: bytes) -> dict[str, bytes]:
    if len(blob) > MAX_JAR:
        raise ValueError('SWT JAR exceeds size limit')
    with zipfile.ZipFile(io.BytesIO(blob)) as jar:
        infos = jar.infolist()
        if len(infos) > 6000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError('SWT expanded content exceeds limit')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate JAR entry')
        for info in infos:
            name = info.filename
            if (name.startswith('/') or '\\' in name or '\0' in name
                    or '..' in name.split('/') or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError('Unsafe JAR entry name or symbolic link')
        return {i.filename: jar.read(i) for i in infos if not i.is_dir()}


def _sections(raw: bytes) -> list[dict[str, str]]:
    """Parse standard manifest continuation lines, rejecting ambiguities."""
    if len(raw) > 2_000_000:
        raise ValueError('Manifest exceeds limit')
    text = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    sections: list[dict[str, str]] = []
    section: dict[str, str] = {}
    current: bytes | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        if b': ' not in current:
            raise ValueError('Malformed manifest attribute')
        key, value = current.split(b': ', 1)
        key_text = key.decode('ascii').lower()
        if not re.fullmatch(r'[a-z0-9_-]+', key_text) or key_text in section:
            raise ValueError('Duplicate or invalid manifest attribute')
        section[key_text] = value.decode('utf-8')
        current = None

    for line in text.split(b'\n'):
        if not line:
            flush()
            if section:
                sections.append(section)
                section = {}
        elif line.startswith(b' '):
            if current is None:
                raise ValueError('Orphan manifest continuation')
            current += line[1:]
        else:
            flush()
            current = line
    flush()
    if section:
        sections.append(section)
    if not sections:
        raise ValueError('Missing manifest attributes')
    return sections


def _signature_name(name: str) -> bool:
    name = name.upper()
    if not name.startswith('META-INF/') or name.count('/') != 1:
        return False
    tail = name.split('/', 1)[1]
    return tail.endswith(('.SF', '.RSA', '.DSA', '.EC')) or tail.startswith('SIG-')


def _digest_matches(attributes: dict[str, str], suffix: str, data: bytes) -> bool:
    # Do not relax weak/disabled algorithms or Java security properties.
    for algorithm in ('sha-256', 'sha-384', 'sha-512'):
        value = attributes.get(algorithm + suffix)
        if value is None:
            continue
        try:
            expected = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError('Malformed signature digest') from exc
        actual = hashlib.new(algorithm.replace('-', ''), data).digest()
        if actual == expected:
            return True
    return False


def validate_replacement(original: bytes, candidate: bytes) -> dict:
    """Accept only the same SWT code restored to its originally signed content."""
    before, after = _members(original), _members(candidate)
    sig_before = {n: b for n, b in before.items() if _signature_name(n)}
    sig_after = {n: b for n, b in after.items() if _signature_name(n)}
    if not sig_before or sig_before != sig_after:
        raise ValueError('Signature metadata differs from pinned SDK; refusing replacement')
    if not any(n.upper().endswith(('.RSA', '.DSA', '.EC')) for n in sig_after):
        raise ValueError('Missing signature block')
    manifest = after.get('META-INF/MANIFEST.MF')
    if manifest is None:
        raise ValueError('Missing exact META-INF/MANIFEST.MF')
    sections = _sections(manifest)
    main = sections[0]
    if (main.get('bundle-symbolicname', '').split(';', 1)[0].strip() != BUNDLE_ID
            or main.get('bundle-version') != BUNDLE_VERSION):
        raise ValueError('Not the pinned SWT GTK x86_64 bundle/version')
    sf_names = [n for n in sig_after if n.upper().endswith('.SF')]
    if not sf_names:
        raise ValueError('Missing signature file')
    for name in sf_names:
        if not _digest_matches(_sections(sig_after[name])[0], '-digest-manifest', manifest):
            raise ValueError('Manifest does not match pinned signed digest: ' + name)
    entries: dict[str, dict[str, str]] = {}
    for section in sections[1:]:
        name = section.get('name')
        if not name or name in entries:
            raise ValueError('Missing/duplicate signed entry name')
        entries[name] = section
    signed_count = 0
    for name, data in after.items():
        if name == 'META-INF/MANIFEST.MF' or _signature_name(name):
            continue
        if name not in entries or not _digest_matches(entries[name], '-digest', data):
            raise ValueError('Unsigned or modified JAR member: ' + name)
        signed_count += 1
    if any(name not in after for name in entries):
        raise ValueError('Missing signed JAR member')
    executable = lambda members: {n: b for n, b in members.items() if n.endswith(('.class', '.so'))}
    code_before, code_after = executable(before), executable(after)
    if not code_before or code_before != code_after:
        raise ValueError('SWT class/native-library bytes differ from pinned SDK')
    if 'org/eclipse/swt/internal/gtk/OS.class' not in after:
        raise ValueError('GTK class missing')
    native = [b for n, b in after.items() if n.endswith('.so')]
    if not native or any(len(b) < 20 or b[:6] != b'\x7fELF\x02\x01'
                         or int.from_bytes(b[18:20], 'little') != 62 for b in native):
        raise ValueError('Native code is not ELF64 x86_64')
    return {'bundle': BUNDLE_ID, 'version': BUNDLE_VERSION,
            'class_and_native_entries_identical': len(code_after),
            'signature_metadata_identical': True, 'signed_members_checked': signed_count,
            'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
            'signatures_removed': False, 'vendor_code_changed': False,
            'certificate_revocation_checked': False}
