"""Fail closed on prohibited tracked content. Heuristic guard, not a security certification."""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

BINARY_SUFFIXES = {'.exe', '.dll', '.so', '.jar', '.class', '.zip', '.xz', '.gz', '.7z',
                   '.ttf', '.otf', '.woff', '.woff2', '.db', '.sqlite', '.sqlite3', '.jfr',
                   '.pem', '.p12', '.pfx', '.jks', '.key'}
PRIVATE_PARTS = {'state', 'session', 'sessions', 'runs', 'logs', '.ssh', '.venv', 'node_modules'}
PATTERNS = {
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github_token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,})\b'),
    'model_key': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b'),
    'cloud_key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
}

def inspect(path: str, data: bytes) -> list[str]:
    p = PurePosixPath(path)
    issues = []
    if p.is_absolute() or '..' in p.parts or '\\' in path:
        issues.append('unsafe_path')
    if any(part.lower() in PRIVATE_PARTS for part in p.parts):
        issues.append('private_runtime_directory')
    if p.name.lower().startswith('.env') or p.name.lower() == 'access-tokens.txt':
        issues.append('credential_file')
    if p.suffix.lower() in BINARY_SUFFIXES:
        issues.append('binary_or_private_material_requires_separate_review')
    if len(data) > 2_000_000:
        issues.append('file_size_limit')
    for name, pattern in PATTERNS.items():
        if pattern.search(data):
            issues.append(name)
    return issues

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0')
    findings = []
    checked = 0
    for name in filter(None, names):
        p = root / name
        if p.is_symlink() or not p.is_file():
            findings.append({'path': name, 'issues': ['not_a_regular_tracked_file']})
            continue
        issues = inspect(name, p.read_bytes())
        checked += 1
        if issues:
            findings.append({'path': name, 'issues': issues})
    print(json.dumps({'status': 'blocked' if findings else 'passed', 'checked_files': checked,
                      'findings': findings, 'security_certification': False}, ensure_ascii=False, indent=2))
    return 2 if findings else 0

if __name__ == '__main__':
    sys.exit(main())
