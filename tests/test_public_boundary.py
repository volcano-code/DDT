from pathlib import Path
import importlib.util
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_guard', ROOT/'tools/check_public_tree.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
spec2 = importlib.util.spec_from_file_location('public_fetch', ROOT/'ci/fetch_public_swt.py')
fetcher = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(fetcher)

class PublicBoundary(unittest.TestCase):
    def test_source_allowed(self):
        self.assertEqual(guard.inspect('client/java/port/Test.java', b'public class Test {}'), [])
    def test_commercial_executable_blocked(self):
        self.assertTrue(guard.inspect('ddt_installer.exe', b'MZ'))
    def test_nested_archives_blocked(self):
        for path in ('source.zip', 'input.tar.gz', 'a.xz', 'a.jar'):
            self.assertTrue(guard.inspect(path, b'test'))
    def test_fonts_blocked(self):
        for suffix in ('ttf','otf','woff','woff2'):
            self.assertTrue(guard.inspect('font.'+suffix, b'test'))
    def test_runtime_state_blocked(self):
        self.assertTrue(guard.inspect('workbench/state/test.json', b'{}'))
    def test_credentials_file_blocked(self):
        self.assertTrue(guard.inspect('.env.production', b'TEST=1'))
    def test_model_secret_blocked(self):
        self.assertIn('model_key', guard.inspect('test.py', ('sk-'+'a'*45).encode()))
    def test_github_secret_blocked(self):
        self.assertIn('github_token', guard.inspect('test.py', ('ghp_'+'A'*40).encode()))
    def test_private_key_blocked(self):
        self.assertIn('private_key', guard.inspect('test.txt', ('-----BEGIN '+'PRIVATE KEY-----').encode()))
    def test_parent_traversal_blocked(self):
        self.assertTrue(guard.inspect('../outside', b'test'))
    def test_official_https_allowed(self):
        self.assertTrue(fetcher.allowed(fetcher.URL))
    def test_redirect_boundaries(self):
        for url in ('http://download.eclipse.org/a','https://evil.example/a',
                    'https://download.eclipse.org.evil.example/a',
                    'https://user:pass@download.eclipse.org/a','https://archive.eclipse.org:444/a'):
            self.assertFalse(fetcher.allowed(url), url)

if __name__ == '__main__':
    unittest.main()
