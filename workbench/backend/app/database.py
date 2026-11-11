"""SQLite prototype persistence. One DB transaction guards review and allocation.

No PostgreSQL compatibility claim is made in v0.2.0. Storage encryption, OIDC and
production migrations are explicitly outside this local synthetic-data release.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA='''
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT OR IGNORE INTO schema_versions VALUES(2, strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE IF NOT EXISTS batches(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,owner TEXT NOT NULL,account TEXT NOT NULL,kind TEXT NOT NULL,
 filename TEXT NOT NULL,sha256 TEXT NOT NULL,raw BLOB NOT NULL,options TEXT NOT NULL,parsed TEXT NOT NULL,
 normalized TEXT NOT NULL,issues TEXT NOT NULL,completeness TEXT NOT NULL,fingerprint TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'preview',created_at TEXT NOT NULL,
 UNIQUE(tenant,owner,sha256,options)
);
CREATE TABLE IF NOT EXISTS records(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,kind TEXT NOT NULL,namespace TEXT NOT NULL,
 external_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,payload_hash TEXT NOT NULL,
 current INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,
 UNIQUE(tenant,account,kind,namespace,external_id,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS current_record ON records(tenant,account,kind,namespace,external_id) WHERE current=1;
CREATE TABLE IF NOT EXISTS sources(
 record_id TEXT NOT NULL REFERENCES records(id),batch_id TEXT NOT NULL REFERENCES batches(id),
 row_number INTEGER NOT NULL,sheet TEXT NOT NULL,PRIMARY KEY(record_id,batch_id,row_number)
);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,scope TEXT NOT NULL,snapshot TEXT NOT NULL,
 rule_version TEXT NOT NULL,created_at TEXT NOT NULL,summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),tenant TEXT NOT NULL,account TEXT NOT NULL,
 business_id TEXT NOT NULL REFERENCES records(id),fund_ids TEXT NOT NULL,category TEXT NOT NULL,
 reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS allocations(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,proposal_id TEXT NOT NULL REFERENCES proposals(id),
 business_id TEXT NOT NULL REFERENCES records(id),fund_id TEXT NOT NULL REFERENCES records(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS proposal_parts(
 proposal_id TEXT NOT NULL REFERENCES proposals(id), fund_id TEXT NOT NULL REFERENCES records(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), evidence_ref TEXT NOT NULL,
 PRIMARY KEY(proposal_id,fund_id)
);
CREATE TABLE IF NOT EXISTS assistant_tasks(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,actor TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES runs(id),
 mode TEXT NOT NULL,query_hash TEXT NOT NULL,plan TEXT NOT NULL,snapshot TEXT NOT NULL,
 status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,result TEXT NOT NULL,trace TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(tenant,actor,idempotency_key)
);
CREATE TABLE IF NOT EXISTS voucher_policies(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,business_namespace TEXT NOT NULL,
 config TEXT NOT NULL,config_hash TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,
 created_by TEXT NOT NULL,approved_by TEXT,approval_reason TEXT,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vouchers(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES runs(id),
 proposal_id TEXT NOT NULL REFERENCES proposals(id),business_id TEXT NOT NULL REFERENCES records(id),
 policy_id TEXT NOT NULL REFERENCES voucher_policies(id),snapshot TEXT NOT NULL,payload TEXT NOT NULL,
 status TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_actions(
 tenant TEXT NOT NULL,actor TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 response TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(tenant,actor,idempotency_key)
);
CREATE TABLE IF NOT EXISTS reviews(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,actor TEXT NOT NULL,proposal_id TEXT NOT NULL REFERENCES proposals(id),
 decision TEXT NOT NULL,reason TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 response TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(tenant,actor,idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,actor TEXT NOT NULL,
 action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL
);
'''

class Database:
    def __init__(self,path:Path):
        self.path=path;path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c: c.executescript(SCHEMA)
        try: path.chmod(0o600)
        except OSError: pass
    def connect(self):
        c=sqlite3.connect(str(self.path),timeout=15,isolation_level=None)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=15000');c.execute('PRAGMA journal_mode=WAL')
        return c
    @contextmanager
    def transaction(self):
        c=self.connect()
        try:
            c.execute('BEGIN IMMEDIATE');yield c;c.commit()
        except Exception: c.rollback();raise
        finally:c.close()
    @contextmanager
    def read(self):
        c=self.connect()
        try:yield c
        finally:c.close()
