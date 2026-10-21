"""Real JDK checks using only self-written classes and synthetic exit scenarios."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]

class PublicJfr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('javac')
        if not compiler:
            raise unittest.SkipTest('JDK required; not counted as exit validation')
        cls.jdk = Path(compiler).resolve().parents[1]
        cls.temp = tempfile.TemporaryDirectory(prefix='ddt-public-jfr-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.classes = cls.base/'classes'
        cls.classes.mkdir()
        fixture = cls.base/'ExitFixture.java'
        fixture.write_text('''package port;
public class ExitFixture {
 public static void main(String[] args) {
  ExitRecording.start();
  if (args[0].equals("exit0")) System.exit(0);
  if (args[0].equals("exit7")) System.exit(7);
  if (args[0].equals("halt9")) Runtime.getRuntime().halt(9);
 }
}''')
        cls.env = {'PATH':str(cls.jdk/'bin')+':/usr/bin:/bin', 'HOME':str(cls.base),
                   'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8'}
        subprocess.run([str(cls.jdk/'bin/javac'),'--release','17','-d',str(cls.classes),
                        str(ROOT/'client/java/port/ExitRecording.java'),
                        str(ROOT/'client/java/port/ExitRecordingReader.java'),str(fixture)],
                       check=True,capture_output=True,text=True,timeout=30,env=cls.env)

    def run_case(self, mode):
        directory = self.base/uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        record = directory/'exit.jfr'
        run_id = uuid.uuid4().hex
        p = subprocess.Popen([str(self.jdk/'bin/java'),'-cp',str(self.classes),
                              '-Dddt.port.run.id='+run_id,'-Dddt.port.exit.file='+str(record),
                              'port.ExitFixture',mode],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,env=self.env)
        try:
            stdout,stderr = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill();p.communicate();raise
        self.assertIn('DDT_EXIT_CAPTURE_READY',stdout)
        return record,run_id,p.pid,p.returncode

    def read(self, record, run_id, pid):
        return subprocess.run([str(self.jdk/'bin/java'),'-cp',str(self.classes),
                               'port.ExitRecordingReader',str(record),run_id,str(pid)],
                              capture_output=True,text=True,timeout=15,env=self.env)

    def verify_case(self, mode, exit_code, mechanism):
        record,run_id,pid,actual = self.run_case(mode)
        self.assertEqual(actual,exit_code)
        result = self.read(record,run_id,pid)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        data = json.loads(result.stdout.split('DDT_EXIT_JSON=',1)[1])
        self.assertEqual(data['run_id'],run_id)
        self.assertEqual(data['pid'],pid)
        self.assertEqual(data['mechanism'],mechanism)
        self.assertFalse(data['business_verified'])

    def test_normal_return(self):
        self.verify_case('return',0,'non_daemon_threads_finished')
    def test_explicit_zero_is_not_business_success(self):
        self.verify_case('exit0',0,'explicit_system_exit')
    def test_explicit_nonzero(self):
        self.verify_case('exit7',7,'explicit_system_exit')
    def test_halt_never_fabricates_shutdown(self):
        record,run_id,pid,code = self.run_case('halt9')
        self.assertEqual(code,9)
        self.assertNotEqual(self.read(record,run_id,pid).returncode,0)
    def test_replayed_identity_rejected(self):
        record,run_id,pid,_ = self.run_case('return')
        self.assertNotEqual(self.read(record,uuid.uuid4().hex,pid).returncode,0)
        self.assertNotEqual(self.read(record,run_id,pid+1).returncode,0)
