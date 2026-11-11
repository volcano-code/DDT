"""Auxiliary voucher drafts, gated by explicit policy and human-approved matches.

Not a statutory accounting rules engine. The only supported transformation is
an explicitly approved receipt-component mapping; no interest calculation,
recognition, tax treatment, or chart of accounts is inferred.
"""
from __future__ import annotations
import csv
import io
import json
from typing import Literal
from pydantic import Field, model_validator
from .domain import StrictModel, DomainError, day, digest
from .service import now, uid, js, audit, cash, cents

class PolicyCreate(StrictModel):
    name: str = Field(min_length=2,max_length=80)
    account: str = Field(min_length=1,max_length=80)
    business_namespace: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    effective_start: str
    effective_end: str
    bank_debit: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,40}$')
    principal_credit: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,40}$')
    interest_credit: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,40}$')
    fee_credit: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,40}$')
    document_ref: str = Field(min_length=2,max_length=200)
    accounting_basis_attested: bool
    is_demo: bool = True
    recognition_basis: Literal['approved_receipt_component_mapping'] = 'approved_receipt_component_mapping'
    @model_validator(mode='after')
    def validate_policy(self):
        self.effective_start=day(self.effective_start);self.effective_end=day(self.effective_end)
        if self.effective_start>self.effective_end:raise ValueError('Invalid policy period.')
        if not self.document_ref.strip() or not self.name.strip():raise ValueError('Missing policy reference.')
        if not self.accounting_basis_attested:raise ValueError('Accounting treatment must be explicitly attested.')
        if self.bank_debit in (self.principal_credit,self.interest_credit,self.fee_credit):
            raise ValueError('Debit cash account must differ from credit accounts.')
        return self

class DecisionRequest(StrictModel):
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8,max_length=120)
    reason: str = Field(min_length=2,max_length=400)
    decision: Literal['approve','reject'] = 'approve'
    @model_validator(mode='after')
    def reason_required(self):
        self.reason=self.reason.strip()
        if len(self.reason)<2:raise ValueError('A meaningful reason is required.')
        return self

class GenerateRequest(StrictModel):
    run_id: str = Field(min_length=1,max_length=64)
    policy_id: str = Field(min_length=1,max_length=64)
    idempotency_key: str = Field(min_length=8,max_length=120)


def demo_policy(account='TEST-001',namespace='combined-demo-v1'):
    return {'name':'合成演示映射（非企业核算政策）','account':account,'business_namespace':namespace,
            'effective_start':'2026-09-01','effective_end':'2026-09-30',
            'bank_debit':'DEMO_BANK','principal_credit':'DEMO_PRINCIPAL',
            'interest_credit':'DEMO_INTEREST','fee_credit':'DEMO_FEE',
            'document_ref':'SYNTHETIC-DEMO-POLICY-ONLY','accounting_basis_attested':True,'is_demo':True}


def action_replay(c,p,key,rh):
    old=c.execute('SELECT * FROM workflow_actions WHERE tenant=? AND actor=? AND idempotency_key=?',(p.tenant,p.user_id,key)).fetchone()
    if old:
        if old['request_hash']!=rh:raise DomainError('idempotency_conflict','防重复键已被其他操作使用。',409)
        return json.loads(old['response'])
    return None


def save_action(c,p,key,rh,response):
    c.execute('INSERT INTO workflow_actions VALUES(?,?,?,?,?,?)',(p.tenant,p.user_id,key,rh,js(response),now()))


class VoucherService:
    def __init__(self,service):self.svc=service;self.db=service.db
    def _policy(self,p,pid,c):
        r=c.execute('SELECT * FROM voucher_policies WHERE id=? AND tenant=?',(pid,p.tenant)).fetchone()
        if not r:raise DomainError('not_found','映射政策不存在。',404)
        p.account(r['account']);d=dict(r);d['config']=json.loads(d['config']);return d
    def policies(self,p):
        with self.db.read() as c:
            ids=c.execute('SELECT id,account FROM voucher_policies WHERE tenant=? ORDER BY created_at DESC LIMIT 100',(p.tenant,)).fetchall()
            return [self._policy(p,r['id'],c) for r in ids if r['account'] in p.accounts]
    def create_policy(self,p,req:PolicyCreate):
        p.require('reviewer');p.account(req.account)
        if req.is_demo and p.tenant!='DEMO':raise DomainError('demo_policy_scope','演示映射只能用于DEMO租户。',403)
        # Deliberately avoid real chart defaults and never interpret demo codes as approved real policy.
        if not req.is_demo and any(getattr(req,f).startswith('DEMO_') for f in ('bank_debit','principal_credit','interest_credit','fee_credit')):
            raise DomainError('demo_codes','真实政策不能使用演示科目。')
        config=req.model_dump();h=digest(config)
        with self.db.transaction() as c:
            old=c.execute("SELECT id FROM voucher_policies WHERE tenant=? AND account=? AND config_hash=? AND status IN ('draft','approved')",(p.tenant,req.account,h)).fetchone()
            if old:return self._policy(p,old['id'],c)|{'duplicate':True}
            pid=uid()
            c.execute('INSERT INTO voucher_policies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (pid,p.tenant,req.account,req.business_namespace,js(config),h,'draft',1,p.user_id,None,None,now()))
            audit(c,p,req.account,'policy.create',pid,{'hash':h,'is_demo':req.is_demo})
            return self._policy(p,pid,c)|{'duplicate':False}
    def decide_policy(self,p,pid,req:DecisionRequest):
        p.require('reviewer');rh=digest({'action':'policy.decision','id':pid,**req.model_dump()})
        with self.db.transaction() as c:
            policy=self._policy(p,pid,c)
            previous=action_replay(c,p,req.idempotency_key,rh)
            if previous:return {'policy':policy,'duplicate':True,'historical_result':previous}
            if policy['version']!=req.expected_version or policy['status']!='draft':raise DomainError('policy_conflict','政策已变更或已审核。',409)
            state='approved' if req.decision=='approve' else 'rejected'
            if state=='approved':
                old_ids=[r['id'] for r in c.execute("SELECT id FROM voucher_policies WHERE tenant=? AND account=? AND business_namespace=? AND status='approved'",(p.tenant,policy['account'],policy['business_namespace']))]
                for old_id in old_ids:
                    c.execute("UPDATE voucher_policies SET status='superseded',version=version+1 WHERE id=?",(old_id,))
                    c.execute("UPDATE vouchers SET status='stale',version=version+1 WHERE policy_id=? AND status IN ('draft','reviewed')",(old_id,))
            c.execute('UPDATE voucher_policies SET status=?,version=version+1,approved_by=?,approval_reason=? WHERE id=?',(state,p.user_id,req.reason,pid))
            audit(c,p,policy['account'],'policy.'+req.decision,pid,{'reason':req.reason,'policy_hash':policy['config_hash']})
            response={'id':pid,'status':state};save_action(c,p,req.idempotency_key,rh,response)
            return {'policy':self._policy(p,pid,c),'duplicate':False}
    def _voucher(self,p,vid,c):
        r=c.execute('SELECT * FROM vouchers WHERE id=? AND tenant=?',(vid,p.tenant)).fetchone()
        if not r:raise DomainError('not_found','凭证草稿不存在。',404)
        p.account(r['account']);d=dict(r);d['payload']=json.loads(d['payload'])
        policy=self._policy(p,d['policy_id'],c);run=self.svc.run_view(p,d['run_id'],c)
        pr=next((x for x in run['proposals'] if x['id']==d['proposal_id']),None)
        d['stale']=(d['status']=='stale' or policy['status']!='approved' or run['stale'] or not pr or
                    pr['status']!='approved' or d['snapshot']!=self.voucher_snapshot(run,pr,policy))
        d['effective_status']='stale' if d['stale'] else d['status']
        d['officially_posted']=False
        return d
    def get(self,p,vid):
        with self.db.read() as c:return self._voucher(p,vid,c)
    def list(self,p,run_id=None):
        with self.db.read() as c:
            if run_id:self.svc.run_view(p,run_id,c)
            rows=c.execute('SELECT id,account FROM vouchers WHERE tenant=?'+(' AND run_id=?' if run_id else '')+' ORDER BY created_at DESC LIMIT 300',
                           (p.tenant,run_id) if run_id else (p.tenant,)).fetchall()
            return [self._voucher(p,r['id'],c) for r in rows if r['account'] in p.accounts]
    @staticmethod
    def voucher_snapshot(run,pr,policy):
        return digest({'run':run['snapshot'],'proposal':pr['id'],'version':pr['version'],
                       'status':pr['status'],'policy':policy['id'],'policy_hash':policy['config_hash'],
                       'policy_version':policy['version']})
    def generate(self,p,req:GenerateRequest):
        p.require('operator');rh=digest({'action':'voucher.generate',**req.model_dump()})
        with self.db.transaction() as c:
            run=self.svc.run_view(p,req.run_id,c);policy=self._policy(p,req.policy_id,c)
            old=action_replay(c,p,req.idempotency_key,rh)
            if old:
                return {'vouchers':[self._voucher(p,v,c) for v in old['ids']], 'skipped':old['skipped'],'duplicate':True}
            if run['stale']:raise DomainError('stale_snapshot','源数据或规则已变化，请重新核对审核。',409)
            if policy['status']!='approved':raise DomainError('policy_not_approved','须由人工审核科目映射政策后才能生成草稿。',409)
            cfg=policy['config']
            if run['account']!=policy['account'] or run['scope']['business_namespace']!=policy['business_namespace']:
                raise DomainError('policy_scope','政策账户或业务命名空间不匹配。',409)
            ids=[];skipped=[]
            for pr in run['proposals']:
                b=pr['business'];d=b['data'];why=None
                if pr['status']!='approved':why='matching_not_approved'
                elif pr['category'] not in ('exact','split','combined') or d['business_type']!='redeem' or d['direction']!='in':why='unsupported_business'
                dates=[d['date']]+[f['data']['date'] for f in pr['funds']]
                if not why and any(not cfg['effective_start']<=dt<=cfg['effective_end'] for dt in dates):why='outside_policy_period'
                if not why and len({dt[:7] for dt in dates})!=1:why='cross_period_requires_separate_policy'
                if why:skipped.append({'event_id':d['record_id'],'reason':why});continue
                amount=cents(d['amount'])
                allocated=c.execute('SELECT coalesce(sum(amount_cents),0) FROM allocations WHERE tenant=? AND proposal_id=? AND active=1',(p.tenant,pr['id'])).fetchone()[0]
                if allocated!=amount:raise DomainError('allocation_incomplete','审核分配金额不完整，禁止制证。',409)
                existing=c.execute("SELECT id FROM vouchers WHERE tenant=? AND business_id=? AND status IN ('draft','reviewed')",(p.tenant,b['id'])).fetchall()
                valid=[]
                for ex in existing:
                    view=self._voucher(p,ex['id'],c)
                    if view['stale']:c.execute("UPDATE vouchers SET status='stale',version=version+1 WHERE id=?",(ex['id'],))
                    else:valid.append(ex['id'])
                if valid:ids.append(valid[0]);continue
                lines=[{'account_code':cfg['bank_debit'],'component':'receipt','debit':d['amount'],'credit':'0.00'}]
                for component in ('principal','interest','fee'):
                    if cents(d[component])>0:
                        lines.append({'account_code':cfg[component+'_credit'],'component':component,'debit':'0.00','credit':d[component]})
                debit=sum(cents(x['debit']) for x in lines);credit=sum(cents(x['credit']) for x in lines)
                if debit!=credit or debit!=amount:raise DomainError('unbalanced_voucher','借贷金额或收款总额不一致。',409)
                evidence={'business':{'id':b['id'],'event_id':d['record_id'],'version':b['version'],'sources':b['sources']},
                          'funds':[{'id':f['id'],'record_id':f['data']['record_id'],'sources':f['sources']} for f in pr['funds']],
                          'allocation_plan':pr['allocation_plan']}
                payload={'purpose':'辅助凭证草稿，非正式记账','is_demo':cfg['is_demo'],
                    'business_date':d['date'],'fund_dates':sorted(set(dates[1:])),
                    'accounting_period':d['date'][:7],'summary':'依据已审核收款组成生成的辅助草稿',
                    'lines':lines,'total_debit':cash(debit),'total_credit':cash(credit),
                    'policy_hash':policy['config_hash'],'policy_document_ref':cfg['document_ref'],
                    'recognition_basis':cfg['recognition_basis'],'evidence':evidence,
                    'limitations':['不重算利费、不推断收入确认时点或税额。','不写入典当通或正式账套。','科目与处理口径来自用户审核的映射，不构成通用核算建议。']}
                vid=uid();snapshot=self.voucher_snapshot(run,pr,policy)
                c.execute('INSERT INTO vouchers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(vid,p.tenant,run['account'],run['id'],pr['id'],b['id'],policy['id'],snapshot,js(payload),'draft',1,now()))
                ids.append(vid)
            result={'ids':ids,'skipped':skipped};save_action(c,p,req.idempotency_key,rh,result)
            audit(c,p,run['account'],'voucher.generate',run['id'],{'count':len(ids),'policy_id':policy['id'],'skipped':len(skipped)})
            return {'vouchers':[self._voucher(p,v,c) for v in ids],'skipped':skipped,'duplicate':False}
    def decide(self,p,vid,req:DecisionRequest):
        p.require('reviewer');rh=digest({'action':'voucher.review','id':vid,**req.model_dump()})
        with self.db.transaction() as c:
            v=self._voucher(p,vid,c);previous=action_replay(c,p,req.idempotency_key,rh)
            if previous:return {'voucher':v,'duplicate':True,'historical_result':previous}
            if v['stale']:raise DomainError('stale_snapshot','草稿依据已过期，不能复核。',409)
            if v['status']!='draft' or v['version']!=req.expected_version:raise DomainError('review_conflict','草稿已变更或已复核。',409)
            status='reviewed' if req.decision=='approve' else 'rejected'
            c.execute('UPDATE vouchers SET status=?,version=version+1 WHERE id=?',(status,vid))
            audit(c,p,v['account'],'voucher.'+req.decision,vid,{'reason':req.reason,'snapshot':v['snapshot']})
            save_action(c,p,req.idempotency_key,rh,{'id':vid,'status':status})
            return {'voucher':self._voucher(p,vid,c),'duplicate':False}
    def export(self,p,vid,fmt):
        with self.db.transaction() as c:
            v=self._voucher(p,vid,c)
            if v['stale'] or v['status']=='rejected':raise DomainError('stale_or_rejected','过期或驳回草稿不能导出为当前凭证草稿。',409)
            if fmt=='json':body=json.dumps(v,ensure_ascii=False,indent=2).encode();media='application/json'
            elif fmt=='csv':
                buf=io.StringIO(newline='');w=csv.writer(buf)
                w.writerow(['用途','草稿ID','状态','期间','事件','科目','组成','借方','贷方','政策依据','业务来源'])
                def safe(x):
                    t=str(x)
                    return "'"+t if t.lstrip().startswith(('=','+','-','@')) or t.startswith(('\t','\r','\n')) else t
                source='; '.join(s['filename']+'|'+s['sheet']+'|行'+str(s['row_number'])+'|SHA256:'+s['sha256'] for s in v['payload']['evidence']['business']['sources'])
                for line in v['payload']['lines']:
                    w.writerow([safe(x) for x in ['辅助凭证草稿，非正式记账',v['id'],v['effective_status'],v['payload']['accounting_period'],v['payload']['evidence']['business']['event_id'],line['account_code'],line['component'],line['debit'],line['credit'],v['payload']['policy_document_ref'],source]])
                body=('\ufeff'+buf.getvalue()).encode();media='text/csv; charset=utf-8'
            else:raise DomainError('export_format','只支持csv或json。')
            audit(c,p,v['account'],'voucher.export',vid,{'format':fmt,'status':v['effective_status']})
            return body,media
