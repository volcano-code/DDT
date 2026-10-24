"""New-release tests use synthetic data and protocol mocks, not vendor APIs."""
import copy
import concurrent.futures
import json
import os
import pytest
import httpx
from conftest import *
from app.assistant import ModelPlanner
from app.vouchers import demo_policy

HEADERS=FUND_HEADERS+['allocation_refs','allocation_evidence']

def combined_rows():
    bs=[dict(BUSINESS[0],record_id='C001',ticket_id='000201',amount='6000.00',principal='5800.00',interest='150.00',fee='50.00',counterparty_ref='MASK-C'),
        dict(BUSINESS[0],record_id='C002',ticket_id='000202',amount='4000.00',principal='3900.00',interest='80.00',fee='20.00',counterparty_ref='MASK-C')]
    fs=[dict(FUNDS[0],record_id='CF001',event_ref='',amount='10000.00',counterparty_ref='MASK-C',
        allocation_refs=json.dumps([{'event_ref':'C001','amount':'6000.00'},{'event_ref':'C002','amount':'4000.00'}]),
        allocation_evidence='SYNTHETIC-ALLOCATION-001')]
    return bs,fs

def load_combined(c,bs=None,fs=None,**scope):
    default_bs,default_fs=combined_rows();bs=bs or default_bs;fs=fs or default_fs
    for kind,rows,headers in [('business',bs,BUS_HEADERS),('fund',fs,HEADERS)]:
        b=preview(c,data=csv_bytes(rows,headers),kind=kind)
        assert not [i for i in b['issues'] if i['severity']=='error'],b
        assert confirm(c,b).status_code==200
    r=c.post('/api/reconcile',json=scope);assert r.status_code==200,r.text
    return r.json()

def approved_combined(c):
    r=load_combined(c)
    for i,p in enumerate(r['proposals']):assert approve(c,p,key='combo-review-'+str(i)).status_code==200
    return c.get('/api/runs/'+r['id']).json()

def policy(c,approved=True,**kw):
    cfg={**demo_policy(namespace='internal-demo-v1'),**kw}
    r=c.post('/api/voucher-policies',json=cfg);assert r.status_code==200,r.text
    p=r.json()
    if approved:
        r=c.post('/api/voucher-policies/'+p['id']+'/review',json={'expected_version':p['version'],'idempotency_key':'approve-policy-'+p['id'],'reason':'合成规则经人工确认'})
        assert r.status_code==200,r.text;p=r.json()['policy']
    return p

def generate(c,r,p,key='generate-key-001'):
    return c.post('/api/vouchers/generate',json={'run_id':r['id'],'policy_id':p['id'],'idempotency_key':key})

def task(c,run,query='汇总当前核对结果',key='task-request-001',mode='local',consent=False):
    return c.post('/api/assistant/tasks',json={'run_id':run['id'],'query':query,'idempotency_key':key,'mode':mode,'consent_to_model':consent})

def execute(c,t,**kw):
    return c.post('/api/assistant/tasks/'+t['id']+'/execute',json={'expected_version':t['version'],**kw})

def test_many_to_one_per_event_allocation(client):
    r=load_combined(client);assert [p['category'] for p in r['proposals']]==['combined','combined']
    assert [p['proposed_amount'] for p in r['proposals']]==['6000.00','4000.00']
    assert all(p['funds'][0]['data']['amount']=='10000.00' for p in r['proposals'])
    assert approve(client,r['proposals'][0],key='one-review-key').status_code==200
    run=client.get('/api/runs/'+r['id']).json();assert run['fund_summary']['remaining']=='4000.00'
    assert approve(client,r['proposals'][1],key='two-review-key').status_code==200
    run=client.get('/api/runs/'+r['id']).json();assert run['fund_summary']['remaining']=='0.00'
    with client.app.state.db.read() as c:
        assert sorted(x[0] for x in c.execute('SELECT amount_cents FROM allocations WHERE active=1'))==[400000,600000]

def test_combined_concurrent_sibling_reviews_conserve(client):
    r=load_combined(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(lambda i:approve(client,r['proposals'][i],key=f'parallel-combo-{i}').status_code,[0,1]))
    assert codes==[200,200]
    assert client.get('/api/runs/'+r['id']).json()['fund_summary']['allocated']=='10000.00'

def test_combined_csv_uses_share_not_whole_fund(client):
    r=load_combined(client);text=client.get('/api/runs/'+r['id']+'/export').text
    assert '本事件分配金额' in text
    # The two proposal export rows must not each claim the full fund amount.
    assert text.count('10000.00')==0

@pytest.mark.parametrize('change,code',[
    ({'allocation_evidence':''},'allocation_evidence_required'),
    ({'event_ref':'C001'},'allocation_conflict'),
    ({'allocation_refs':'invalid'},'invalid_allocation_refs'),
    ({'allocation_refs':'[]'},'invalid_allocation_refs'),
    ({'allocation_refs':'[{"event_ref":"C001","amount":"6000.00"},{"event_ref":"C002","amount":"3000.00"}]'},'allocation_sum'),
    ({'allocation_refs':'[{"event_ref":"C001","amount":"5000.00"},{"event_ref":"C001","amount":"5000.00"}]'},'duplicate_allocation_ref'),
    ({'allocation_refs':'[{"event_ref":"C001","amount":6000},{"event_ref":"C002","amount":"4000.00"}]'},'invalid_allocation_refs'),
    ({'allocation_refs':'[{"event_ref":"C001","amount":"NaN"},{"event_ref":"C002","amount":"4000.00"}]'},'invalid_amount'),
    ({'allocation_refs':'[{"event_ref":"C001","amount":"0.00"},{"event_ref":"C002","amount":"10000.00"}]'},'zero_amount'),
])
def test_bad_shared_allocations_block_import(client,change,code):
    bs,fs=combined_rows();fs[0].update(change)
    b=preview(client,data=csv_bytes(fs,HEADERS),kind='fund')
    assert code in [x['code'] for x in b['issues']]
    assert confirm(client,b).status_code==409

def test_missing_sibling_blocks_whole_shared_fund(client):
    bs,fs=combined_rows();r=load_combined(client,bs=bs[:1],fs=fs)
    assert r['proposals'][0]['category']=='allocation_scope_incomplete'
    assert approve(client,r['proposals'][0]).status_code==409

def test_conflicted_sibling_blocks_both(client):
    bs,fs=combined_rows();bs[1]['counterparty_ref']='OTHER'
    r=load_combined(client,bs,fs);assert all(p['category']=='allocation_scope_incomplete' for p in r['proposals'])

def test_overlap_run_cannot_reclaim_shared_fund(client):
    r=approved_combined(client)
    other=client.post('/api/reconcile',json={'period_end':'2026-09-29'}).json()
    assert approve(client,other['proposals'][0],key='another-combined-run').status_code==409

def test_source_correction_invalidates_shared_allocations(client):
    r=approved_combined(client);bs,fs=combined_rows();fs[0]['note']='更正合成来源说明'
    b=preview(client,data=csv_bytes(fs,HEADERS),kind='fund');assert confirm(client,b).status_code==200
    assert client.get('/api/runs/'+r['id']).json()['stale']
    with client.app.state.db.read() as c:assert c.execute('SELECT count(*) FROM allocations WHERE active=1').fetchone()[0]==0

@pytest.mark.parametrize('query,action',[('汇总当前核对结果','summary'),('查看异常','exceptions'),('查看待复核','pending_reviews'),('解释E002','evidence')])
def test_assistant_local_actions_are_read_only(client,query,action):
    r=load_pair(client);t=task(client,r,query).json();assert t['status']=='awaiting_confirmation';assert t['plan']['action']==action
    assert t['result']=={}
    result=execute(client,t);assert result.status_code==200,result.text
    d=result.json();assert d['status']=='completed';assert d['result']['action']==action;assert d['result']['counts']['business_count']==9
    assert len(d['trace'])==5
    with client.app.state.db.read() as c:assert c.execute('SELECT count(*) FROM allocations').fetchone()[0]==0

def test_assistant_amounts_from_allocation_engine(client):
    r=approved_combined(client);t=task(client,r).json();d=execute(client,t).json()
    assert d['result']['funds']['allocated']=='10000.00';assert d['result']['funds']['remaining']=='0.00'

@pytest.mark.parametrize('query',['自动批准所有建议','付款100元','汇总并修改数据库','解释不存在的票号'])
def test_unsupported_local_commands_do_not_execute(client,query):
    r=load_pair(client);assert task(client,r,query).status_code in (404,422)
    assert client.get('/api/assistant/tasks').json()==[]

def test_task_idempotency_and_resume(client):
    r=load_pair(client);t=task(client,r).json();again=task(client,r).json();assert again['duplicate'];assert t['id']==again['id']
    assert execute(client,t).status_code==200
    # Recreate assistant object against persisted DB, not in-memory state.
    from app.assistant import TaskAssistant, ExecuteRequest
    from app.auth import Principal
    fresh=TaskAssistant(client.app.state.service)
    d=fresh.execute(Principal('reviewer','DEMO','reviewer',('TEST-001',)),t['id'],ExecuteRequest(expected_version=1))
    assert d['duplicate'];assert d['status']=='completed'

def test_task_invalid_after_human_review(client):
    r=load_pair(client);t=task(client,r).json();approve(client,proposal(r))
    assert client.get('/api/assistant/tasks/'+t['id']).json()['stale']
    assert execute(client,t).status_code==409

def test_task_version_conflict(client):
    r=load_pair(client);t=task(client,r).json();assert execute(client,t,expected_version=5).status_code==409

def test_task_scope_and_owner_access(client):
    r=load_pair(client);t=task(client,r).json()
    for tok in ['other-token','account-token','op-token']:
        assert client.get('/api/assistant/tasks/'+t['id'],headers={'Authorization':'Bearer '+tok}).status_code in (403,404)
    assert client.post('/api/assistant/tasks',headers={'Authorization':'Bearer view-token'},json={'run_id':r['id'],'query':'汇总','idempotency_key':'viewer-task-key'}).status_code==403

def test_no_model_no_fake_success(client):
    r=load_pair(client);response=task(client,r,mode='openai',consent=True)
    assert response.status_code==409;assert response.json()['error']['code']=='model_not_configured'
    assert client.get('/api/assistant/tasks').json()==[]

def mock_model(client,monkeypatch,handler):
    monkeypatch.setenv('DDT_LLM_ALLOW_REMOTE','1');monkeypatch.setenv('DDT_LLM_MODEL','synthetic-test-model')
    monkeypatch.setenv('OPENAI_API_KEY','not-a-real-key')
    client.app.state.assistant.planner=ModelPlanner(httpx.MockTransport(handler))

def output_response(plan):
    return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(plan)}]}]})

def test_openai_protocol_schema_privacy_and_confirmation(client,monkeypatch):
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content));assert str(request.url)=='https://api.openai.com/v1/responses'
        return output_response({'action':'evidence','proposal_position':2})
    mock_model(client,monkeypatch,handler);r=load_pair(client)
    t=task(client,r,'解释E002',mode='openai',consent=True)
    assert t.status_code==200,t.text;t=t.json();assert t['mode']=='openai';assert t['status']=='awaiting_confirmation'
    assert sent[0]['store'] is False;assert sent[0]['text']['format']['strict'] is True
    assert '第2条建议' in sent[0]['input'];assert 'E002' not in json.dumps(sent)
    assert '发送全部客户' not in json.dumps(sent);assert '5300.00' not in json.dumps(sent)
    result=execute(client,t).json();assert result['result']['items'][0]['event_id']=='E002'
    assert result['result']['items'][0]['amount']=='5300.00'

@pytest.mark.parametrize('bad_plan',[{'action':'approve','proposal_position':None}, {'action':'evidence','proposal_position':999},
    {'action':'summary','proposal_position':None,'amount':'999999.00'},{'action':'evidence','proposal_position':True},
    {'action':'evidence','proposal_position':None},{}])
def test_invalid_model_plan_rejected(client,monkeypatch,bad_plan):
    mock_model(client,monkeypatch,lambda _:output_response(bad_plan));r=load_pair(client)
    assert task(client,r,mode='openai',consent=True).status_code==502
    assert client.get('/api/assistant/tasks').json()==[]

def test_model_timeout_does_not_leak_payload(client,monkeypatch):
    def timeout(req):raise httpx.ReadTimeout('secret credentials test',request=req)
    mock_model(client,monkeypatch,timeout);r=load_pair(client)
    res=task(client,r,mode='openai',consent=True);assert res.status_code==503
    assert 'secret' not in res.text

def test_model_consent_required_before_transport(client,monkeypatch):
    mock_model(client,monkeypatch,lambda _:pytest.fail('network must not run'))
    r=load_pair(client);assert task(client,r,mode='openai').status_code==409

def test_ollama_transport_is_loopback(client,monkeypatch):
    monkeypatch.setenv('DDT_OLLAMA_MODEL','synthetic-test')
    def handler(req):
        assert str(req.url)=='http://127.0.0.1:11434/api/chat';b=json.loads(req.content);assert b['stream'] is False
        return httpx.Response(200,json={'done':True,'message':{'content':'{"action":"summary","proposal_position":null}'}})
    client.app.state.assistant.planner=ModelPlanner(httpx.MockTransport(handler))
    r=load_pair(client);t=task(client,r,mode='ollama',consent=True);assert t.status_code==200,t.text
    assert execute(client,t.json()).status_code==200

def test_draft_policy_requires_explicit_approval(client):
    r=approved_combined(client);p=policy(client,approved=False)
    res=generate(client,r,p);assert res.status_code==409;assert res.json()['error']['code']=='policy_not_approved'

def test_policy_does_not_approve_matches(client):
    r=load_combined(client);p=policy(client)
    res=generate(client,r,p);assert res.status_code==200
    assert res.json()['vouchers']==[];assert len(res.json()['skipped'])==2

def test_vouchers_balanced_and_components_not_all_income(client):
    r=approved_combined(client);p=policy(client);res=generate(client,r,p);assert res.status_code==200,res.text
    vs=res.json()['vouchers'];assert len(vs)==2
    assert [v['payload']['total_debit'] for v in vs]==['6000.00','4000.00']
    for v in vs:
        data=v['payload'];assert data['total_debit']==data['total_credit'];assert not v['officially_posted']
        assert {l['component'] for l in data['lines']}=={'receipt','principal','interest','fee'}
        assert data['evidence']['allocation_plan'][0]['amount']==data['total_debit']
        assert data['evidence']['business']['sources'];assert v['status']=='draft'

def test_voucher_generation_idempotent_even_new_key(client):
    r=approved_combined(client);p=policy(client)
    a=generate(client,r,p).json();b=generate(client,r,p).json();cc=generate(client,r,p,key='other-generate-key').json()
    assert b['duplicate'];assert [v['id'] for v in a['vouchers']]==[v['id'] for v in cc['vouchers']]
    assert len(client.get('/api/vouchers').json())==2

def test_policy_supersession_invalidates_drafts(client):
    r=approved_combined(client);p=policy(client);vs=generate(client,r,p).json()['vouchers']
    p2=policy(client,name='更新后的合成政策',fee_credit='DEMO_FEE_V2')
    assert p2['id']!=p['id']
    assert all(client.get('/api/vouchers/'+v['id']).json()['stale'] for v in vs)
    assert client.get('/api/vouchers/'+vs[0]['id']+'/export').status_code==409
    v2=generate(client,r,p2,key='new-policy-generation').json()['vouchers'];assert len(v2)==2
    assert v2[0]['id']!=vs[0]['id']

def test_source_correction_invalidates_vouchers(client):
    r=approved_combined(client);p=policy(client);v=generate(client,r,p).json()['vouchers'][0]
    bs,_=combined_rows();bs[0]['note']='来源更正'
    b=preview(client,rows=bs);confirm(client,b)
    assert client.get('/api/vouchers/'+v['id']).json()['effective_status']=='stale'
    assert client.get('/api/vouchers/'+v['id']+'/export').status_code==409
    replay=generate(client,r,p).json();assert replay['duplicate'];assert replay['vouchers'][0]['stale']

def test_voucher_review_and_export(client):
    r=approved_combined(client);p=policy(client);v=generate(client,r,p).json()['vouchers'][0]
    req={'expected_version':1,'idempotency_key':'voucher-review-001','reason':'已核验合成科目与来源'}
    res=client.post('/api/vouchers/'+v['id']+'/review',json=req);assert res.status_code==200,res.text
    d=res.json()['voucher'];assert d['status']=='reviewed';assert not d['officially_posted']
    assert client.post('/api/vouchers/'+v['id']+'/review',json=req).json()['duplicate']
    text=client.get('/api/vouchers/'+v['id']+'/export?format=csv').text
    assert '非正式记账' in text;assert 'SHA256:' in text;assert 'DEMO_PRINCIPAL' in text

def test_policy_scope_mismatch_blocks_draft(client):
    r=approved_combined(client);p=policy(client,business_namespace='different-ns')
    assert generate(client,r,p).status_code==409

def test_policy_time_bounds_skip_unsupported_records(client):
    r=approved_combined(client);p=policy(client,effective_start='2026-10-01',effective_end='2026-10-31')
    res=generate(client,r,p);assert res.status_code==200
    assert res.json()['vouchers']==[];assert all(x['reason']=='outside_policy_period' for x in res.json()['skipped'])

def test_cross_month_draft_is_not_guessed(client):
    b=preview(client,rows=[BUSINESS[0]]);confirm(client,b)
    b=preview(client,rows=[dict(FUNDS[0],date='2026-10-01')],kind='fund',period_end='2026-10-02');confirm(client,b)
    r=client.post('/api/reconcile',json={'fund_period_end':'2026-10-02'}).json();assert approve(client,proposal(r)).status_code==200
    p=policy(client,effective_end='2026-10-31');result=generate(client,r,p).json()
    assert result['vouchers']==[];assert result['skipped'][0]['reason']=='cross_period_requires_separate_policy'

def test_no_default_real_policy_or_inferred_accounting(client):
    assert client.get('/api/voucher-policies').json()==[]
    cfg=demo_policy(namespace='internal-demo-v1');cfg['accounting_basis_attested']=False
    assert client.post('/api/voucher-policies',json=cfg).status_code==422
    cfg['accounting_basis_attested']=True;cfg['is_demo']=False
    assert client.post('/api/voucher-policies',json=cfg).status_code==422

def test_policy_and_draft_roles(client):
    r=approved_combined(client);p=policy(client);v=generate(client,r,p).json()['vouchers'][0]
    assert client.post('/api/voucher-policies',headers={'Authorization':'Bearer op-token'},json=demo_policy()).status_code==403
    assert client.get('/api/vouchers/'+v['id'],headers={'Authorization':'Bearer other-token'}).status_code==404
    assert client.get('/api/vouchers/'+v['id'],headers={'Authorization':'Bearer account-token'}).status_code==403
    assert client.post('/api/vouchers/'+v['id']+'/review',headers={'Authorization':'Bearer op-token'},json={'expected_version':1,'reason':'测试审核','idempotency_key':'bad-operator-review'}).status_code==403

def test_additive_database_upgrade_preserves_existing_records(client):
    r=load_pair(client);approve(client,proposal(r));db=client.app.state.db
    from app.database import Database
    Database(db.path)
    assert client.get('/api/runs/'+r['id']).json()['proposals'][0]['status']=='approved'
    with db.read() as c:assert c.execute('SELECT version FROM schema_versions').fetchone()[0]==2

def test_rule_upgrade_releases_old_allocations_before_new_run(client,monkeypatch):
    r=load_pair(client);assert approve(client,proposal(r)).status_code==200
    import app.service as svc
    monkeypatch.setattr(svc,'RULE_VERSION','test-next-rule')
    other=client.post('/api/reconcile',json={});assert other.status_code==200
    assert approve(client,proposal(other.json()),key='review-after-upgrade').status_code==200
    with client.app.state.db.read() as c:
        assert c.execute('SELECT count(*) FROM allocations WHERE active=1').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM allocations WHERE active=0').fetchone()[0]==1
