import json
from conftest import *
from app.domain import BUSINESS as INTERNAL_BUSINESS

def test_unknown_mapping_blocks_confirmation(client):
    data=csv_bytes(BUSINESS[:1],BUS_HEADERS).replace(b'record_id',b'CUSTOM_ID',1)
    b=preview(client,data=data);assert any(i['code']=='mapping_required' for i in b['issues'])
    assert confirm(client,b).status_code==409
    mapping={x:x for x in INTERNAL_BUSINESS};mapping['record_id']='CUSTOM_ID'
    b=preview(client,data=data,mapping=mapping)
    assert confirm(client,b).status_code==200

def test_missing_and_nan_do_not_become_zero(client):
    b=preview(client,data=(ROOT/'samples/business_invalid.csv').read_bytes())
    assert b['valid_count']==0;assert confirm(client,b).status_code==409

def test_same_file_upload_is_idempotent(client):
    a=preview(client);b=preview(client);assert a['id']==b['id'];assert b['duplicate']
    assert confirm(client,a).status_code==200
    assert confirm(client,b).json()['duplicate']

def test_overlapping_export_keeps_event_versions_and_provenance(client):
    a=preview(client);confirm(client,a)
    b=preview(client,rows=BUSINESS[:2],name='overlap.csv');r=confirm(client,b).json()
    assert r['identical']==2;assert r['inserted']==0
    with client.app.state.db.read() as c:
        assert c.execute('SELECT count(*) FROM records WHERE current=1').fetchone()[0]==9
        rid=c.execute("SELECT id FROM records WHERE external_id='E001'").fetchone()[0]
        assert c.execute('SELECT count(*) FROM sources WHERE record_id=?',(rid,)).fetchone()[0]==2

def test_same_ticket_distinct_events_not_deduplicated(client):
    b=preview(client);confirm(client,b)
    with client.app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM records WHERE external_id IN ('E001','E006') AND current=1").fetchone()[0]==2

def test_duplicate_event_id_same_batch_blocked(client):
    b=preview(client,rows=[BUSINESS[0],BUSINESS[0]])
    assert any(i['code']=='duplicate_key' for i in b['issues']);assert confirm(client,b).status_code==409

def test_control_count_mismatch_blocks_import(client):
    b=preview(client,expected_count=10);assert confirm(client,b).status_code==409

def test_control_total_mismatch_blocks_import(client):
    b=preview(client,expected_total='0');assert confirm(client,b).status_code==409

def test_complete_claim_requires_count_total_and_scope(client):
    b=preview(client,expected_count=9,expected_total='41870',full_scope_attested=True,scope_note='合成样例已全量导出')
    assert b['completeness']=='attested';assert confirm(client,b,acknowledge_unknown_completeness=False).status_code==200

def test_unknown_completeness_requires_ack(client):
    b=preview(client);assert b['completeness']=='unknown'
    assert confirm(client,b,acknowledge_unknown_completeness=False).status_code==409
    assert confirm(client,b).status_code==200

def test_stale_preview_fingerprint_rejected(client):
    b=preview(client);assert confirm(client,b,fingerprint='changed').status_code==409

def test_amount_components_must_add_up(client):
    b=preview(client,rows=[dict(BUSINESS[0],principal='9999')]);assert confirm(client,b).status_code==409

def test_record_outside_selected_period_rejected(client):
    b=preview(client,rows=[dict(BUSINESS[0],date='2026-08-31')]);assert confirm(client,b).status_code==409

def test_records_wrong_scope_rejected(client):
    b=preview(client,rows=[dict(BUSINESS[0],account='OTHER')]);assert confirm(client,b).status_code==409

def test_source_pagination_retains_rows(client):
    b=preview(client);r=client.get('/api/batches/'+b['id']+'/rows?offset=2&limit=2').json()
    assert r['total']==9;assert r['rows'][0]['row']==4
    assert client.get('/api/batches/'+b['id']+'/rows?offset=-1').status_code==422
