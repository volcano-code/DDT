import hashlib,sys,json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from app.main import create_app
from sample_data import BUSINESS,FUNDS,BUS_HEADERS,FUND_HEADERS,csv_bytes

@pytest.fixture
def client(tmp_path):
    entries=[]
    for token,user,tenant,role,accounts in [('review-token','reviewer','DEMO','reviewer',['TEST-001']),('op-token','operator','DEMO','operator',['TEST-001']),('view-token','viewer','DEMO','viewer',['TEST-001']),('other-token','other','OTHER','reviewer',['TEST-001']),('account-token','acct','DEMO','reviewer',['OTHER-ACCOUNT'])]:
        entries.append({'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'user_id':user,'tenant':tenant,'role':role,'accounts':accounts})
    with TestClient(create_app(tmp_path/'test.sqlite3',entries)) as c:
        c.headers['Authorization']='Bearer review-token';yield c

def preview(c,rows=None,kind='business',data=None,name='input.csv',**opts):
    if data is None:data=csv_bytes(rows or (BUSINESS if kind=='business' else FUNDS),BUS_HEADERS if kind=='business' else FUND_HEADERS)
    r=c.post('/api/batches/preview',files={'file':(name,data)},data={'options':json.dumps({'kind':kind,**opts})})
    assert r.status_code==200,r.text
    return r.json()

def confirm(c,b,**kw):
    return c.post('/api/batches/'+b['id']+'/confirm',json={'fingerprint':b['fingerprint'],'acknowledge_unknown_completeness':True,**kw})

def load_pair(c):
    for kind in ['business','fund']:
        b=preview(c,kind=kind);r=confirm(c,b);assert r.status_code==200,r.text
    r=c.post('/api/reconcile',json={});assert r.status_code==200,r.text;return r.json()

def proposal(run,event='E001'):
    return next(p for p in run['proposals'] if p['business']['data']['record_id']==event)

def approve(c,p,key='unique-request-key',**kw):
    return c.post('/api/proposals/'+p['id']+'/review',json={'decision':'approve','expected_version':p['version'],'reason':'人工核对了原始单据','idempotency_key':key,**kw})
