from conftest import *

def test_missing_token_denied(client):
    client.headers.pop('Authorization');assert client.get('/api/batches').status_code==401

def test_wrong_token_denied(client):
    client.headers['Authorization']='Bearer wrong';assert client.get('/api/meta').status_code==401

def test_viewer_cannot_import_or_reconcile(client):
    client.headers['Authorization']='Bearer view-token';assert client.post('/api/reconcile',json={}).status_code==403
    r=client.post('/api/batches/preview',files={'file':('x.csv',b'x\na')},data={'options':'{"kind":"business"}'})
    assert r.status_code==403

def test_operator_cannot_review(client):
    p=proposal(load_pair(client));client.headers['Authorization']='Bearer op-token';assert approve(client,p).status_code==403

def test_other_tenant_cannot_read_batch_run_or_review(client):
    r=load_pair(client);b=client.get('/api/batches').json()[0];p=proposal(r)
    client.headers['Authorization']='Bearer other-token'
    assert client.get('/api/batches/'+b['id']).status_code==404
    assert client.get('/api/runs/'+r['id']).status_code==404
    assert approve(client,p).status_code==404
    assert client.get('/api/batches').json()==[]

def test_account_scope_enforced(client):
    r=load_pair(client);client.headers['Authorization']='Bearer account-token'
    assert client.get('/api/runs/'+r['id']).status_code==403
    assert client.post('/api/reconcile',json={}).status_code==403
    assert client.get('/api/batches').json()==[]

def test_no_writeback_or_fake_api(client):
    assert client.get('/api/integrations/diandang').json()['status']=='not_configured'
    assert client.post('/api/pay',json={}).status_code==405 or client.post('/api/pay',json={}).status_code==404
    assert client.get('/api/meta').json()['llm']=='not_connected'

def test_no_tokens_in_metadata_or_audit(client):
    load_pair(client)
    for path in ['/api/meta','/api/batches','/api/audit','/api/schema']:
        text=client.get(path).text;assert 'review-token' not in text;assert 'token_sha256' not in text

def test_file_size_limit(client):
    r=client.post('/api/batches/preview',files={'file':('big.csv',b'x'*(9*1024*1024+1))},data={'options':'{"kind":"business"}'})
    assert r.status_code==413

def test_security_headers(client):
    r=client.get('/health');assert r.headers['x-content-type-options']=='nosniff';assert r.headers['cache-control']=='no-store'
    assert "object-src 'none'" in r.headers['content-security-policy']

def test_invalid_host_denied(client):assert client.get('/health',headers={'Host':'evil.example'}).status_code==400

def test_invalid_request_does_not_echo_secret(client):
    r=client.post('/api/reconcile',json={'unexpected':'secret-private-value'});assert r.status_code==422;assert 'secret-private-value' not in r.text
