"""Real child processes, not mock-success parsing; no business data or network."""
import json,os,sys,time
from pathlib import Path
from unittest import mock
import pytest
from app import parser_process as pp
from app.domain import DomainError
from app.parsers import parse_file
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('ext',['csv','xls','xlsx'])
def test_real_worker_matches_baseline(ext):
    data=(ROOT/'samples'/('business.'+ext)).read_bytes()
    assert pp.parse_file_isolated(data,'business.'+ext)==parse_file(data,'business.'+ext)

@pytest.mark.parametrize('data,name',[(b'','x.csv'),(b'not excel','x.xlsx'),(b'a,a\n1,2','x.csv')])
def test_bad_input_rejected(data,name):
    with pytest.raises(DomainError):pp.parse_file_isolated(data,name)

@pytest.mark.parametrize('header',[0,True,31,'1'])
def test_header_range(header):
    with pytest.raises(DomainError):pp.parse_file_isolated(b'a\n1','x.csv',header_row=header)

def test_child_environment_no_credentials(tmp_path):
    with mock.patch.dict(os.environ,{'OPENAI_API_KEY':'DO-NOT-SEND','GH_TOKEN':'PRIVATE','PYTHONPATH':'EVIL','JAVA_TOOL_OPTIONS':'EVIL','LD_PRELOAD':'EVIL'}):
        env=pp.worker_environment(tmp_path)
    assert not set(env)&{'OPENAI_API_KEY','GH_TOKEN','PYTHONPATH','JAVA_TOOL_OPTIONS','LD_PRELOAD'}

def test_actual_timeout_child_is_stopped(tmp_path):
    worker=tmp_path/'sleeper.py';worker.write_text('import time\ntime.sleep(40)\n')
    before=time.monotonic()
    with mock.patch.object(pp,'WORKER',worker),mock.patch.object(pp,'WALL_SECONDS',.3):
        with pytest.raises(DomainError) as ex:pp.parse_file_isolated(b'a\n1','x.csv')
    assert ex.value.code=='parse_timeout';assert time.monotonic()-before<5

def test_worker_failure_does_not_leak_error_text(tmp_path):
    worker=tmp_path/'failure.py';worker.write_text("import sys\nsys.stderr.write('SECRET-TEST-ONLY')\nsys.exit(7)\n")
    with mock.patch.object(pp,'WORKER',worker):
        with pytest.raises(DomainError) as ex:pp.parse_file_isolated(b'a\n1','x.csv')
    assert ex.value.code=='parse_worker_failure';assert 'SECRET' not in ex.value.message

def test_output_limit_enforced(tmp_path):
    worker=tmp_path/'oversize.py';worker.write_text("print('x'*2048)\n")
    with mock.patch.object(pp,'WORKER',worker),mock.patch.object(pp,'MAX_OUTPUT',1024):
        with pytest.raises(DomainError) as ex:pp.parse_file_isolated(b'a\n1','x.csv')
    assert ex.value.code=='parse_output_limit'

def test_real_worker_limits_applied_before_parser_import():
    text=(ROOT/'backend/app/parser_worker.py').read_text()
    assert text.index('setrlimit(resource.RLIMIT_AS') < text.index('from app.parsers import parse_file')
    for key in ['RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_CORE','RLIMIT_NOFILE']:assert key in text

def test_service_uses_isolated_entrypoint():
    from app import service
    assert service.parse_file is pp.parse_file_isolated

def test_failed_parser_leaves_no_batch(client):
    worker_error=DomainError('parse_timeout','timeout test',422)
    with mock.patch('app.service.parse_file',side_effect=worker_error):
        r=client.post('/api/batches/preview',files={'file':('x.csv',b'a\n1')},data={'options':json.dumps({'kind':'business'})})
    assert r.status_code==422
    with client.app.state.db.read() as conn:assert conn.execute('SELECT count(*) FROM batches').fetchone()[0]==0
