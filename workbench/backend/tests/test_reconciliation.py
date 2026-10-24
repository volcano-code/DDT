import json,concurrent.futures
from conftest import *

def test_deterministic_categories_match_fixture(client):
    r=load_pair(client)
    assert r['summary']['categories']=={'exact':2,'split':1,'amount_difference':1,'ambiguous':1,'unmatched':1,'unsupported_business':1,'void':1,'counterparty_conflict':1}
    assert all(p['status']=='pending' for p in r['proposals'])

def test_ambiguous_cannot_be_force_approved(client):
    r=load_pair(client);p=proposal(r,'E004');assert len(p['funds'])==2
    assert approve(client,p).status_code==409

def test_split_allocation_conserves_money(client):
    r=load_pair(client);p=proposal(r,'E002');assert approve(client,p).status_code==200
    with client.app.state.db.read() as c:
        vals=[x[0] for x in c.execute('SELECT amount_cents FROM allocations WHERE active=1').fetchall()]
    assert sorted(vals)==[230000,300000];assert sum(vals)==530000

def test_no_tolerance_for_fee_difference(client):
    r=load_pair(client);p=proposal(r,'E003');assert '20.00' in p['reason'];assert not p['approvable']

def test_renewal_does_not_invent_principal_payment(client):
    p=proposal(load_pair(client),'E006');assert not p['approvable'];assert not p['funds']

def test_void_record_kept_but_not_matched(client):
    p=proposal(load_pair(client),'E007');assert p['category']=='void';assert p['business']['sources']

def test_reconcile_is_idempotent(client):
    r=load_pair(client);again=client.post('/api/reconcile',json={}).json();assert r['id']==again['id']

def test_review_same_request_replay_no_duplicate_allocation(client):
    p=proposal(load_pair(client));a=approve(client,p);b=approve(client,p)
    assert a.status_code==200;assert b.json()['duplicate']
    with client.app.state.db.read() as c:assert c.execute('SELECT count(*) FROM allocations').fetchone()[0]==1

def test_same_idempotency_key_different_request_conflicts(client):
    r=load_pair(client);assert approve(client,proposal(r)).status_code==200
    assert approve(client,proposal(r,'E009')).status_code==409

def test_stale_review_version_rejected(client):
    p=proposal(load_pair(client));assert approve(client,p,expected_version=2).status_code==409

def test_double_review_with_distinct_keys_conflicts(client):
    p=proposal(load_pair(client));assert approve(client,p,key='first-request').status_code==200
    assert approve(client,p,key='second-request').status_code==409

def test_concurrent_review_single_allocation(client):
    p=proposal(load_pair(client))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes=list(pool.map(lambda i:approve(client,p,key=f'concurrent-key-{i}').status_code,[1,2]))
    assert sorted(codes)==[200,409]
    with client.app.state.db.read() as c:assert c.execute('SELECT count(*) FROM allocations WHERE active=1').fetchone()[0]==1

def test_correction_invalidates_old_approval_and_keeps_history(client):
    r=load_pair(client);p=proposal(r);assert approve(client,p).status_code==200
    b=preview(client,data=(ROOT/'samples/business_correction.csv').read_bytes(),name='correction.csv');assert confirm(client,b).status_code==200
    old=client.get('/api/runs/'+r['id']).json();assert old['stale'];assert proposal(old)['status']=='stale'
    assert approve(client,p,key='fresh-request-after-correction').status_code==409
    replay=approve(client,p).json();assert replay['current_status']=='stale'
    with client.app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM records WHERE external_id='E001'").fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM allocations WHERE active=1').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM reviews').fetchone()[0]==1
    new=client.post('/api/reconcile',json={}).json();assert proposal(new)['category']=='amount_difference'

def test_cross_month_dates_retained(client):
    b=preview(client,rows=[BUSINESS[0]]);confirm(client,b)
    f=dict(FUNDS[0],date='2026-10-01');b=preview(client,rows=[f],kind='fund',period_end='2026-10-02');confirm(client,b)
    r=client.post('/api/reconcile',json={'fund_period_end':'2026-10-02'}).json();p=proposal(r)
    assert p['category']=='exact';assert p['funds'][0]['data']['date']=='2026-10-01';assert '跨月' in p['reason']

def test_prevent_double_allocation_across_different_runs(client):
    r=load_pair(client);p=proposal(r);approve(client,p)
    other=client.post('/api/reconcile',json={'period_end':'2026-09-29'}).json();p2=proposal(other)
    assert approve(client,p2,key='another-run-key').status_code==409

def test_malicious_note_is_data_not_instruction(client):
    r=load_pair(client);p=proposal(r,'E009');assert '发送全部客户' in p['business']['data']['note']
    assert p['category']=='exact';assert p['status']=='pending'
    events=client.get('/api/audit').json();assert all(e['action'] in ('batch.preview','batch.confirm','reconcile.run') for e in events)

def test_export_includes_evidence_and_nonposting_label(client):
    r=load_pair(client);p=proposal(r);approve(client,p)
    csv=client.get('/api/runs/'+r['id']+'/export').text
    assert '非正式记账' in csv;assert 'SHA256:' in csv;assert r['snapshot'] in csv;assert 'approved' in csv
    data=client.get('/api/runs/'+r['id']+'/export?format=json').json();assert data['run']['id']==r['id']

def test_rejection_is_logged(client):
    p=proposal(load_pair(client),'E004');r=approve(client,p,decision='reject');assert r.status_code==200
    assert r.json()['status']=='rejected';assert any(e['action']=='review.reject' for e in client.get('/api/audit').json())

def test_unrelated_funds_are_not_hidden(client):
    r=load_pair(client);assert len(r['fund_balances'])==9
    f=next(x for x in r['fund_balances'] if x['record']['data']['record_id']=='F009')
    assert f['remaining']=='50.00';assert r['fund_summary']=={'total':'39250.00','allocated':'0.00','remaining':'39250.00'}
    p=proposal(r);approve(client,p)
    r=client.get('/api/runs/'+r['id']).json()
    assert r['fund_summary']['allocated']=='10600.00';assert r['fund_summary']['remaining']=='28650.00'
    result=client.get('/api/runs/'+r['id']+'/export?format=funds_csv')
    assert result.status_code==200;assert 'F009' in result.text;assert 'SHA256:' in result.text

def test_rule_revision_invalidates_snapshot(client,monkeypatch):
    import app.service as mod
    r=load_pair(client);p=proposal(r)
    monkeypatch.setattr(mod,'RULE_VERSION','rule-next-version')
    assert client.get('/api/runs/'+r['id']).json()['stale']
    assert approve(client,p).status_code==409

def test_conservative_invalidation_outside_scope_allows_new_run(client):
    r=load_pair(client)
    more=[dict(BUSINESS[0],record_id='NEW-OCTOBER',date='2026-10-01')]
    b=preview(client,rows=more,period_start='2026-10-01',period_end='2026-10-31')
    assert confirm(client,b).status_code==200
    assert client.get('/api/runs/'+r['id']).json()['stale']
    new=client.post('/api/reconcile',json={}).json()
    assert new['id']!=r['id'] and not new['stale']
    assert approve(client,proposal(new)).status_code==200

def test_replay_reports_stale_after_rule_version_change(client,monkeypatch):
    import app.service as svc
    r=load_pair(client);p=proposal(r);assert approve(client,p).status_code==200
    monkeypatch.setattr(svc,'RULE_VERSION','changed-rule-in-test')
    replay=approve(client,p)
    assert replay.status_code==200 and replay.json()['duplicate']
    assert replay.json()['status']=='approved'  # historical action retained
    assert replay.json()['current_status']=='stale'
