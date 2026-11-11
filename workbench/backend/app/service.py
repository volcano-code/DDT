from __future__ import annotations
from datetime import datetime, timezone, date
from decimal import Decimal
import hashlib, json, uuid
from .domain import *
from .parser_process import parse_file_isolated as parse_file
from .auth import Principal


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return uuid.uuid4().hex
def js(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def cents(amount): return int(Decimal(amount)*100)
def cash(n): return format(Decimal(n)/100,'.2f')

def audit(c,p,account,action,target,detail):
    c.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)',(uid(),p.tenant,account,p.user_id,action,target,js(detail),now()))

class Service:
    def __init__(self,db):self.db=db

    def batch(self,p,batch_id,c=None):
        def read(conn):
            b=conn.execute('SELECT * FROM batches WHERE id=? AND tenant=?',(batch_id,p.tenant)).fetchone()
            if not b: raise DomainError('not_found','批次不存在。',404)
            p.account(b['account']);return dict(b)
        if c is not None:return read(c)
        with self.db.read() as conn:return read(conn)

    def batch_view(self,b):
        parsed=json.loads(b['parsed']); normalized=json.loads(b['normalized']);opts=json.loads(b['options'])
        return {k:b[k] for k in ['id','account','kind','filename','sha256','fingerprint','status','completeness','created_at']} | {
            'options':opts,'format':parsed['format'],'engine':parsed['engine'],'sheet':parsed['sheet'],
            'headers':parsed['headers'],'row_count':len(parsed['rows']),'valid_count':len(normalized),
            'total':cash(sum(cents(r['data']['amount']) for r in normalized)),
            'issues':json.loads(b['issues']),'preview':parsed['rows'][:50],
            'normalized_preview':normalized[:50], 'preview_limit':50,
        }

    def create_batch(self,p:Principal,data:bytes,filename:str,o:ImportOptions):
        p.require('operator');p.account(o.account)
        file_sha=hashlib.sha256(data).hexdigest();opt=js(o.model_dump())
        with self.db.read() as c:
            old=c.execute('SELECT * FROM batches WHERE tenant=? AND owner=? AND sha256=? AND options=?',(p.tenant,p.user_id,file_sha,opt)).fetchone()
            if old:return self.batch_view(dict(old))|{'duplicate':True}
        parsed=parse_file(data,filename,sheet=o.sheet,header_row=o.header_row)
        fields=BUSINESS if o.kind=='business' else FUNDS
        mapping=o.mapping or {k:k for k in fields+OPTIONAL if k in parsed['headers']}
        issues=[dict(x,severity='warning') for x in parsed['warnings']];normalized=[];seen=set()
        if len(set(mapping.values()))!=len(mapping.values()): raise DomainError('mapping_duplicate','同一来源列不能重复映射为不同含义。')
        if any(k not in fields+OPTIONAL for k in mapping):raise DomainError('mapping_unknown','映射含未知内部字段。')
        missing=[k for k in fields if k not in mapping]
        absent=[v for v in mapping.values() if v not in parsed['headers']]
        if missing or absent:
            issues.append({'severity':'error','code':'mapping_required','message':'请明确必填列映射。','fields':missing,'missing_headers':absent})
        else:
            for source in parsed['rows']:
                mapped={k:source['values'][v] for k,v in mapping.items()}
                try:
                    record=normalize(mapped,o)
                    if record['record_id'] in seen:raise DomainError('duplicate_key','同批次事件/流水编号重复；请核实，不静默去重。')
                    seen.add(record['record_id']);normalized.append({'row':source['row'],'data':record})
                except DomainError as e:issues.append({'severity':'error','code':e.code,'row':source['row'],'message':e.message})
        ignored=[h for h in parsed['headers'] if h not in mapping.values()]
        if ignored:issues.append({'severity':'warning','code':'unmapped_columns','message':'以下列仅保留在原始来源，不参加核对。','columns':ignored})
        total=sum(cents(r['data']['amount']) for r in normalized)
        if o.expected_count is not None and o.expected_count!=len(parsed['rows']):issues.append({'severity':'error','code':'count_mismatch','message':'数据条数与来源控制数不一致。'})
        if o.expected_total is not None and cents(o.expected_total)!=total:issues.append({'severity':'error','code':'total_mismatch','message':'金额与来源控制总额不一致，禁止确认。'})
        complete='attested' if o.expected_count is not None and o.expected_total is not None and o.full_scope_attested else 'unknown'
        if complete=='unknown':issues.append({'severity':'warning','code':'completeness_unknown','message':'来源范围或控制总数未齐备：完整性未证实。'})
        # Dates/amounts are internal cash semantics, not inferred accounting entries.
        fingerprint=digest({'sha':file_sha,'options':o.model_dump(),'records':normalized,'issues':issues})
        bid=uid();created=now()
        with self.db.transaction() as c:
            old=c.execute('SELECT * FROM batches WHERE tenant=? AND owner=? AND sha256=? AND options=?',(p.tenant,p.user_id,file_sha,opt)).fetchone()
            if old:return self.batch_view(dict(old))|{'duplicate':True}
            c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                bid,p.tenant,p.user_id,o.account,o.kind,filename,file_sha,data,opt,js(parsed),js(normalized),js(issues),complete,fingerprint,'preview',created
            ))
            audit(c,p,o.account,'batch.preview',bid,{'sha256':file_sha,'rows':len(parsed['rows']),'errors':sum(x['severity']=='error' for x in issues)})
            return self.batch_view(self.batch(p,bid,c))|{'duplicate':False}

    def confirm(self,p,bid,req:ConfirmRequest):
        p.require('operator')
        with self.db.transaction() as c:
            b=self.batch(p,bid,c)
            if req.fingerprint!=b['fingerprint']:raise DomainError('stale_preview','预览指纹不一致，请重新读取。',409)
            if b['status']=='confirmed':return {'id':bid,'status':'confirmed','duplicate':True}
            if any(i['severity']=='error' for i in json.loads(b['issues'])):raise DomainError('batch_errors','存在数据或映射错误，不能确认导入。',409)
            if b['completeness']=='unknown' and not req.acknowledge_unknown_completeness:raise DomainError('ack_required','必须明确确认完整性未证实的限制。',409)
            o=ImportOptions.model_validate_json(b['options']); inserted=0;identical=0
            for item in json.loads(b['normalized']):
                payload=item['data'];h=digest(payload);ext=payload['record_id']
                old=c.execute('SELECT * FROM records WHERE tenant=? AND account=? AND kind=? AND namespace=? AND external_id=? AND current=1',(p.tenant,o.account,o.kind,o.source_namespace,ext)).fetchone()
                if old and old['payload_hash']==h:rid=old['id'];identical+=1
                else:
                    rid=uid();ver=old['version']+1 if old else 1
                    if old:c.execute('UPDATE records SET current=0 WHERE id=?',(old['id'],))
                    c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,p.tenant,o.account,o.kind,o.source_namespace,ext,ver,js(payload),h,1,now()))
                    inserted+=1
                c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?)',(rid,bid,item['row'],json.loads(b['parsed'])['sheet']))
            if inserted:
                c.execute("UPDATE vouchers SET status='stale',version=version+1 WHERE tenant=? AND account=? AND status IN ('draft','reviewed')",(p.tenant,o.account))
                c.execute("UPDATE assistant_tasks SET status='stale',version=version+1 WHERE tenant=? AND account=? AND status IN ('awaiting_confirmation','completed')",(p.tenant,o.account))
                # Conservative invalidation: any account data revision invalidates account reviews.
                c.execute("UPDATE proposals SET status='stale',version=version+1 WHERE tenant=? AND account=? AND status IN ('pending','approved')",(p.tenant,o.account))
                c.execute('UPDATE allocations SET active=0 WHERE tenant=? AND proposal_id IN (SELECT id FROM proposals WHERE tenant=? AND account=? AND status=?)',(p.tenant,p.tenant,o.account,'stale'))
            c.execute("UPDATE batches SET status='confirmed' WHERE id=?",(bid,))
            audit(c,p,o.account,'batch.confirm',bid,{'inserted_versions':inserted,'identical_records':identical,'completeness':b['completeness']})
            return {'id':bid,'status':'confirmed','duplicate':False,'inserted':inserted,'identical':identical}

    def batches(self,p):
        with self.db.read() as c:
            rows=c.execute('SELECT * FROM batches WHERE tenant=? ORDER BY created_at DESC LIMIT 300',(p.tenant,)).fetchall()
            return [self.batch_view(dict(b)) for b in rows if b['account'] in p.accounts]

    def records(self,p,c,scope:ReconcileRequest):
        p.account(scope.account)
        results={}
        for kind,ns,start,end in [('business',scope.business_namespace,scope.period_start,scope.period_end),('fund',scope.fund_namespace,scope.fund_period_start,scope.fund_period_end)]:
            rows=c.execute('SELECT * FROM records WHERE tenant=? AND account=? AND kind=? AND namespace=? AND current=1 ORDER BY external_id',(p.tenant,scope.account,kind,ns)).fetchall()
            kept=[]
            for r in rows:
                d=dict(r);d['data']=json.loads(d.pop('payload'))
                if start<=d['data']['date']<=end:kept.append(d)
            if len(kept)>MAX_ROWS:raise DomainError('scope_limit','本原型每侧最多5000条，需缩小期间。')
            results[kind]=kept
        return results

    def snapshot(self,records,scope):
        return digest({'scope':scope.model_dump(),'rule':RULE_VERSION,'records':[(r['id'],r['payload_hash']) for k in ['business','fund'] for r in records[k]]})

    def reconcile(self,p,scope:ReconcileRequest):
        p.require('operator');p.account(scope.account)
        with self.db.transaction() as c:
            # An upgraded rule must retire old allocations before producing a new run.
            obsolete=c.execute("SELECT count(*) FROM proposals WHERE tenant=? AND account=? AND status IN ('pending','approved') AND run_id IN (SELECT id FROM runs WHERE rule_version<>?)",(p.tenant,scope.account,RULE_VERSION)).fetchone()[0]
            if obsolete:
                c.execute("UPDATE proposals SET status='stale',version=version+1 WHERE tenant=? AND account=? AND status IN ('pending','approved') AND run_id IN (SELECT id FROM runs WHERE rule_version<>?)",(p.tenant,scope.account,RULE_VERSION))
                c.execute("UPDATE allocations SET active=0 WHERE tenant=? AND proposal_id IN (SELECT id FROM proposals WHERE tenant=? AND account=? AND status='stale')",(p.tenant,p.tenant,scope.account))
                c.execute("UPDATE vouchers SET status='stale',version=version+1 WHERE tenant=? AND account=? AND status IN ('draft','reviewed') AND run_id IN (SELECT id FROM runs WHERE rule_version<>?)",(p.tenant,scope.account,RULE_VERSION))
                audit(c,p,scope.account,'rule.invalidate',scope.account,{'new_rule':RULE_VERSION,'proposals':obsolete})
            records=self.records(p,c,scope);snap=self.snapshot(records,scope)
            if not records['business'] or not records['fund']:raise DomainError('missing_side','指定范围内须先导入业务明细和资金流水。',409)
            # Repeated run requests return the same snapshot; review state is retained.
            existing=c.execute("SELECT id FROM runs WHERE tenant=? AND account=? AND snapshot=? AND rule_version=? AND NOT EXISTS (SELECT 1 FROM proposals WHERE proposals.run_id=runs.id AND proposals.status='stale') ORDER BY created_at DESC LIMIT 1",(p.tenant,scope.account,snap,RULE_VERSION)).fetchone()
            if existing:return self.run_view(p,existing['id'],c)
            rid=uid();summary={'business_count':len(records['business']),'fund_count':len(records['fund']),'fund_record_ids':[f['id'] for f in records['fund']],'categories':{},'warning':'仅为业务收付款核对，不是银行对账完成或正式记账。'}
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',(rid,p.tenant,scope.account,js(scope.model_dump()),snap,RULE_VERSION,now(),js(summary)))
            allfunds=records['fund']; related={};candidates={};parts={};shared=set();bad_refs={}
            business_by_ref={x['external_id']:x for x in records['business']}
            for f in allfunds:
                d=f['data'];key=(d['currency'],d['direction'])
                if d['status']!='active':continue
                if d.get('allocation_refs'):
                    refs=parse_allocation_refs(d['allocation_refs']);shared.add(f['id'])
                    invalid=any(e['event_ref'] not in business_by_ref or
                        business_by_ref[e['event_ref']]['data']['currency']!=d['currency'] or
                        business_by_ref[e['event_ref']]['data']['direction']!=d['direction'] or
                        business_by_ref[e['event_ref']]['data']['status']!='active' or
                        business_by_ref[e['event_ref']]['data']['business_type']!='redeem' or
                        (business_by_ref[e['event_ref']]['data']['counterparty_ref'] and d['counterparty_ref'] and
                         business_by_ref[e['event_ref']]['data']['counterparty_ref']!=d['counterparty_ref'])
                        for e in refs)
                    for e in refs:
                        related.setdefault(key+(e['event_ref'],),[]).append(f)
                        parts[(f['id'],e['event_ref'])]=(cents(e['amount']),d['allocation_evidence'])
                        if invalid:bad_refs[f['id']]='分配清单含范围外、作废、不支持或对手方冲突的事件；整笔须补证。'
                elif d['event_ref']:
                    related.setdefault(key+(d['event_ref'],),[]).append(f)
                    parts[(f['id'],d['event_ref'])]=(cents(d['amount']),'明确事件编号')
                else:candidates.setdefault(key+(d['amount'],),[]).append(f)
            for b in records['business']:
                d=b['data'];fs=[];cat='unmatched';reason='未找到有明确事件关联的资金记录。'
                if d['status']=='void':cat='void';reason='源业务已作废；保留来源，不生成核对分配。'
                elif d['business_type']!='redeem' or d['direction']!='in':cat='unsupported_business';reason='本版只自动建议赎当收款；续当、退款等保留为人工核查。'
                elif cents(d['amount'])==0:cat='zero_cash';reason='明确现金金额为零，不推断新增放款或收款。'
                else:
                    fs=related.get((d['currency'],d['direction'],d['record_id']),[])
                    if len(fs)>20:cat='candidate_limit';reason='明确关联流水超过20条，本版不自动分配。'
                    elif fs:
                        value=sum(parts[(f['id'],d['record_id'])][0] for f in fs)
                        counter_conflict=any(d['counterparty_ref'] and f['data']['counterparty_ref'] and d['counterparty_ref']!=f['data']['counterparty_ref'] for f in fs)
                        if any(f['id'] in bad_refs for f in fs):cat='allocation_scope_incomplete';reason='分配清单关联范围不完整或存在冲突，需核实整笔收款。'
                        elif counter_conflict:cat='counterparty_conflict';reason='事件编号一致但对手方标识冲突。'
                        elif value==cents(d['amount']):
                            cat='exact' if len(fs)==1 else 'split';reason='明确事件编号关联；账户、币种、方向及金额一致。' if len(fs)==1 else '明确事件编号关联的分笔收款合计一致；各笔分配金额可追溯。'
                            if any(f['id'] in shared for f in fs):cat='combined';reason='来源明确列出合并收款分配金额及依据；按本事件份额分配，不认领整笔流水。'
                            if any(f['data']['date'][:7]!=d['date'][:7] for f in fs):reason+=' 存在跨月到账，原日期均保留。'
                        else:cat='amount_difference';reason=f'明确关联流水合计{cash(value)}，业务应收{d["amount"]}，差额{cash(value-cents(d["amount"]))}；不自动抹平。'
                    else:
                        possible=candidates.get((d['currency'],d['direction'],d['amount']),[])
                        possible=[f for f in possible if not f['data']['event_ref'] and abs((date.fromisoformat(f['data']['date'])-date.fromisoformat(d['date'])).days)<=3]
                        if possible:
                            fs=possible[:20];cat='ambiguous';reason=f'发现{len(possible)}条近日期同金额记录，但没有事件关联证据；不能自动确认。'
                pid=uid();summary['categories'][cat]=summary['categories'].get(cat,0)+1
                c.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,rid,p.tenant,scope.account,b['id'],js([f['id'] for f in fs[:20]]),cat,reason,'pending',1))
                for f in fs[:20]:
                    part=parts.get((f['id'],d['record_id']))
                    if part:
                        c.execute('INSERT INTO proposal_parts VALUES(?,?,?,?)',(pid,f['id'],part[0],part[1]))
            c.execute('UPDATE runs SET summary=? WHERE id=?',(js(summary),rid))
            audit(c,p,scope.account,'reconcile.run',rid,{'snapshot':snap,'rule_version':RULE_VERSION})
            return self.run_view(p,rid,c)

    def sources(self,c,record_id):
        rows=c.execute('SELECT s.*,b.filename,b.sha256,b.completeness,b.fingerprint FROM sources s JOIN batches b ON b.id=s.batch_id WHERE s.record_id=? ORDER BY b.created_at',(record_id,)).fetchall()
        return [dict(x) for x in rows]
    def record_view(self,c,rid):
        row=c.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
        if not row:raise DomainError('not_found','记录不存在。',404)
        d=dict(row);d['data']=json.loads(d.pop('payload'));d['sources']=self.sources(c,rid);return d
    def proposal_view(self,c,row):
        d=dict(row);d['fund_ids']=json.loads(d['fund_ids'])
        d['business']=self.record_view(c,d['business_id']);d['funds']=[self.record_view(c,x) for x in d['fund_ids']]
        saved={x['fund_id']:dict(x) for x in c.execute('SELECT * FROM proposal_parts WHERE proposal_id=?',(d['id'],))}
        d['allocation_plan']=[{'fund_id':f['id'],'fund_external_id':f['data']['record_id'],
            'amount':cash(saved[f['id']]['amount_cents']) if f['id'] in saved else f['data']['amount'],
            'evidence_ref':saved[f['id']]['evidence_ref'] if f['id'] in saved else '历史单事件关联'} for f in d['funds'] if f['id'] in saved or d['category'] in ('exact','split')]
        d['proposed_amount']=cash(sum(cents(x['amount']) for x in d['allocation_plan']))
        d['approvable']=d['category'] in ('exact','split','combined') and d['status']=='pending'
        return d

    def run_view(self,p,rid,c=None):
        def read(conn):
            row=conn.execute('SELECT * FROM runs WHERE id=? AND tenant=?',(rid,p.tenant)).fetchone()
            if not row:raise DomainError('not_found','核对任务不存在。',404)
            p.account(row['account']);d=dict(row);d['scope']=json.loads(d['scope']);d['summary']=json.loads(d['summary'])
            scope=ReconcileRequest(**d['scope']);d['stale']=d['rule_version']!=RULE_VERSION or self.snapshot(self.records(p,conn,scope),scope)!=d['snapshot']
            d['proposals']=[self.proposal_view(conn,r) for r in conn.execute('SELECT * FROM proposals WHERE run_id=? ORDER BY rowid',(rid,)).fetchall()]
            d['stale']=d['stale'] or any(pr['status']=='stale' for pr in d['proposals'])
            if d['stale']:
                for r in d['proposals']:r['approvable']=False
            # Every source fund row is visible, including rows absent from proposals.
            d['fund_balances']=[]
            for fid in d['summary'].get('fund_record_ids',[]):
                rec=self.record_view(conn,fid)
                allocated=conn.execute('SELECT coalesce(sum(amount_cents),0) FROM allocations WHERE tenant=? AND fund_id=? AND active=1',(p.tenant,fid)).fetchone()[0]
                d['fund_balances'].append({'record':rec,'allocated':cash(allocated),'remaining':cash(cents(rec['data']['amount'])-allocated)})
            d['fund_summary']={'total':cash(sum(cents(x['record']['data']['amount']) for x in d['fund_balances'])),
                'allocated':cash(sum(cents(x['allocated']) for x in d['fund_balances'])),
                'remaining':cash(sum(cents(x['remaining']) for x in d['fund_balances']))}
            return d
        if c is not None:return read(c)
        with self.db.read() as conn:return read(conn)

    def runs(self,p):
        with self.db.read() as c:
            return [dict(r)|{'summary':json.loads(r['summary'])} for r in c.execute('SELECT * FROM runs WHERE tenant=? ORDER BY created_at DESC LIMIT 100',(p.tenant,)).fetchall() if r['account'] in p.accounts]

    def review(self,p,pid,req:ReviewRequest):
        p.require('reviewer');rh=digest({'proposal':pid,**req.model_dump()})
        with self.db.transaction() as c:
            row=c.execute('SELECT * FROM proposals WHERE id=? AND tenant=?',(pid,p.tenant)).fetchone()
            if not row:raise DomainError('not_found','建议不存在。',404)
            p.account(row['account'])
            old=c.execute('SELECT * FROM reviews WHERE tenant=? AND actor=? AND idempotency_key=?',(p.tenant,p.user_id,req.idempotency_key)).fetchone()
            if old:
                if old['request_hash']!=rh:raise DomainError('idempotency_conflict','同一防重复键已用于不同请求。',409)
                # A replay returns original action with present status; never hides later invalidation.
                present='stale' if self.run_view(p,row['run_id'],c)['stale'] else row['status']
                return json.loads(old['response'])|{'duplicate':True,'current_status':present}
            run=self.run_view(p,row['run_id'],c)
            if run['stale'] or row['status']=='stale':raise DomainError('stale_snapshot','源数据或规则变化，必须重新核对。',409)
            if row['version']!=req.expected_version or row['status']!='pending':raise DomainError('review_conflict','建议已变更或已审核，请刷新。',409)
            if req.decision=='approve':
                if row['category'] not in ('exact','split','combined'):raise DomainError('insufficient_evidence','只有明确关联且金额一致的建议可确认；其他需补证或更正源数据。',409)
                b=self.record_view(c,row['business_id']);fs=[self.record_view(c,i) for i in json.loads(row['fund_ids'])]
                allocation_map={x['fund_id']:x['amount_cents'] for x in c.execute('SELECT * FROM proposal_parts WHERE proposal_id=?',(pid,))}
                part_values={f['id']:allocation_map.get(f['id'],cents(f['data']['amount'])) for f in fs}
                if sum(part_values.values())!=cents(b['data']['amount']):
                    raise DomainError('allocation_sum','分配计划与业务金额不一致。',409)
                prior=c.execute('SELECT coalesce(sum(amount_cents),0) FROM allocations WHERE tenant=? AND business_id=? AND active=1',(p.tenant,b['id'])).fetchone()[0]
                if prior+sum(part_values.values())>cents(b['data']['amount']):raise DomainError('over_allocation','业务金额已被认领，拒绝重复分配。',409)
                for f in fs:
                    used=c.execute('SELECT coalesce(sum(amount_cents),0) FROM allocations WHERE tenant=? AND fund_id=? AND active=1',(p.tenant,f['id'])).fetchone()[0]
                    amount=part_values[f['id']]
                    if used+amount>cents(f['data']['amount']):raise DomainError('over_allocation','资金流水已被认领，拒绝重复分配。',409)
                    c.execute('INSERT INTO allocations VALUES(?,?,?,?,?,?,?)',(uid(),p.tenant,pid,b['id'],f['id'],amount,1))
            status='approved' if req.decision=='approve' else 'rejected';ver=row['version']+1
            c.execute('UPDATE proposals SET status=?,version=? WHERE id=?',(status,ver,pid))
            result={'id':pid,'status':status,'version':ver,'duplicate':False,'current_status':status}
            c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),p.tenant,p.user_id,pid,req.decision,req.reason,req.idempotency_key,rh,js(result),now()))
            audit(c,p,row['account'],'review.'+req.decision,pid,{'reason':req.reason,'snapshot':run['snapshot'],'version':ver})
            return result

    def audit_events(self,p):
        with self.db.read() as c:
            return [dict(x)|{'detail':json.loads(x['detail'])} for x in c.execute('SELECT * FROM audit WHERE tenant=? ORDER BY created_at DESC LIMIT 300',(p.tenant,)).fetchall() if x['account'] in p.accounts]
