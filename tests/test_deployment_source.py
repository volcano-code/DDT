"""Public deployment checks: no commercial installer, original class or network use."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'client'))
from ddt_port import dependencies,installer,runtime,startup,toolchain,transactions

class PublicDeployment(unittest.TestCase):
    def test_entrypoint_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,str(ROOT/'client/bin/ddt-linux'),'--version'],cwd=folder,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'0.1.0-alpha.5-dev.1')
    def test_dependency_plan_has_no_side_effects(self):
        with patch.object(dependencies.subprocess,'run') as run:
            p=dependencies.plan(release={'ID':'ubuntu','VERSION_ID':'24.04'},machine='x86_64')
        run.assert_not_called()
        self.assertTrue(p['supported_profile']);self.assertFalse(p['system_changed'])
    def test_unqualified_distribution_is_not_guessed(self):
        p=dependencies.plan(release={'ID':'unknown','VERSION_ID':'24.04'},machine='x86_64')
        self.assertFalse(p['supported_profile']);self.assertEqual(p['commands'],[])
    def test_invalid_archive_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'not-an-installer.exe';file.write_bytes(b'MZ synthetic test')
            target=Path(folder)/'runtime'
            with self.assertRaises(ValueError):installer.prepare(file,target)
            self.assertFalse(target.exists())
    def test_credentials_and_library_injection_are_not_forwarded(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'synthetic','OPENAI_API_KEY':'synthetic','LD_PRELOAD':'bad','DISPLAY':':17'}):
            env=runtime.clean_env(Path('/tmp/synthetic-test'))
        for key in ('GITHUB_TOKEN','OPENAI_API_KEY','LD_PRELOAD'):self.assertNotIn(key,env)
        self.assertEqual(env['DISPLAY'],':17')
    def test_zero_exit_does_not_hide_exception(self):
        r=startup.classify({'started':True,'returncode':0,'output':'java.lang.NullPointerException'})
        self.assertEqual(r['classification'],'startup_error')
        self.assertFalse(r['business_compatible'])
    def test_atomic_write_rejects_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'data';original.write_bytes(b'keep')
            link=Path(folder)/'link';link.symlink_to(original)
            with self.assertRaises(ValueError):transactions.atomic_bytes(link,b'replace')
            self.assertEqual(original.read_bytes(),b'keep')
    def test_tool_path_must_be_explicit_absolute(self):
        with self.assertRaises(ValueError):toolchain.check_binary(Path('java'))

if __name__=='__main__':unittest.main()
