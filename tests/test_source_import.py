"""Ensure the reviewed Java source import is complete and byte-identical to its manifest."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]/'client'

class ReviewedSourceImport(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT/'SOURCE_IMPORT.json').read_text())

    def test_exact_source_set(self):
        actual = {p.relative_to(ROOT).as_posix() for p in (ROOT/'java').rglob('*.java')}
        self.assertEqual(set(self.manifest['files']), actual)
        self.assertEqual(len(actual), 20)

    def test_no_escaping_or_symlink_paths(self):
        for name in self.manifest['files']:
            rel = PurePosixPath(name)
            self.assertFalse(rel.is_absolute())
            self.assertNotIn('..', rel.parts)
            self.assertTrue(name.startswith('java/') and name.endswith('.java'))
            self.assertFalse((ROOT/name).is_symlink())

    def test_every_source_digest_matches(self):
        for name, expected in self.manifest['files'].items():
            self.assertRegex(expected, r'^[a-f0-9]{64}$')
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), expected, name)
