"""Internal prototype schema. These are NOT official 典当通 API fields."""
from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
import hashlib, json, re

VERSION = '0.2.1-dev.1'
RULE_VERSION = 'explicit-link-2.0'
MAX_ROWS = 5000
MAX_COLUMNS = 80
MAX_BYTES = 8 * 1024 * 1024

class DomainError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

def money(value: object, *, allow_zero: bool = True) -> str:
    """Strict CNY cash scale; never infer a missing value or silently round."""
    text = str(value).strip() if value is not None else ''
    if not re.fullmatch(r'(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?', text):
        raise DomainError('invalid_amount', '金额必须为非负十进制文本，最多两位小数；不允许缺失、千分位、NaN或负数。')
    try: d = Decimal(text)
    except InvalidOperation: raise DomainError('invalid_amount', '金额无法解析。')
    if not allow_zero and d == 0: raise DomainError('zero_amount', '此记录金额必须大于零。')
    return format(d.quantize(Decimal('.01')), '.2f')

def digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def day(value: str) -> str:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise DomainError('invalid_date', '日期须为 YYYY-MM-DD；不猜测日/月顺序。')
    try: return date.fromisoformat(value).isoformat()
    except ValueError: raise DomainError('invalid_date', '日期无效。')

COMMON = ['record_id','account','currency','direction','date','amount','status']
BUSINESS = COMMON + ['ticket_id','business_type','principal','interest','fee']
FUNDS = COMMON + ['event_ref']
OPTIONAL = ['counterparty_ref','note','reversal_of','allocation_refs','allocation_evidence']
LABELS = {
 'record_id':'事件/流水唯一编号','account':'账户标识','currency':'币种','direction':'收支方向(in/out)',
 'date':'业务/入账日期','amount':'明确的应收付/实收付金额','status':'状态(active/void)',
 'ticket_id':'当票号','business_type':'业务类型(redeem/renew/refund/other)',
 'principal':'本金组成','interest':'利息组成','fee':'费用组成','event_ref':'明确关联的业务事件编号',
 'counterparty_ref':'脱敏对手方标识','note':'备注(不作为指令)','reversal_of':'原交易编号',
 'allocation_refs':'合并收款分配JSON（明确事件及金额）','allocation_evidence':'合并收款分配依据编号'
}

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ImportOptions(StrictModel):
    kind: Literal['business','fund']
    source_namespace: str = Field(default='internal-demo-v1', pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    sheet: str | None = None
    header_row: int = Field(default=1, ge=1, le=30)
    mapping: dict[str,str] = Field(default_factory=dict)
    account: str = Field(default='TEST-001', min_length=1,max_length=80)
    currency: Literal['CNY'] = 'CNY'
    direction: Literal['in','out'] = 'in'
    period_start: str = '2026-09-01'
    period_end: str = '2026-09-30'
    expected_count: int | None = Field(default=None,ge=0,le=MAX_ROWS)
    expected_total: str | None = None
    full_scope_attested: bool = False
    scope_note: str = Field(default='',max_length=300)
    @model_validator(mode='after')
    def validate_period(self):
        self.period_start, self.period_end = day(self.period_start), day(self.period_end)
        if self.period_start > self.period_end: raise ValueError('开始日期不能晚于结束日期。')
        if self.expected_total is not None: self.expected_total = money(self.expected_total)
        if self.full_scope_attested and not self.scope_note.strip(): raise ValueError('完整范围声明需要来源依据。')
        return self

class ConfirmRequest(StrictModel):
    fingerprint: str
    acknowledge_unknown_completeness: bool = False

class ReconcileRequest(StrictModel):
    account: str = 'TEST-001'
    business_namespace: str = 'internal-demo-v1'
    fund_namespace: str = 'internal-demo-v1'
    period_start: str = '2026-09-01'
    period_end: str = '2026-09-30'
    fund_period_start: str = '2026-09-01'
    fund_period_end: str = '2026-09-30'
    @model_validator(mode='after')
    def dates(self):
        for a,b in [('period_start','period_end'),('fund_period_start','fund_period_end')]:
            setattr(self,a,day(getattr(self,a))); setattr(self,b,day(getattr(self,b)))
            if getattr(self,a)>getattr(self,b): raise ValueError('日期范围无效。')
        return self

class ReviewRequest(StrictModel):
    decision: Literal['approve','reject']
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8,max_length=120)
    reason: str = Field(min_length=2,max_length=400)
    @model_validator(mode='after')
    def real_reason(self):
        self.reason=self.reason.strip()
        if len(self.reason)<2: raise ValueError('审核理由不能仅含空白。')
        return self


def normalize(row: dict[str, str], options: ImportOptions) -> dict:
    required = BUSINESS if options.kind == 'business' else FUNDS
    out = {k: str(row.get(k,'')).strip() for k in required + OPTIONAL}
    for k in COMMON + (['ticket_id','business_type'] if options.kind=='business' else []):
        if not out[k]: raise DomainError('missing_field', f'缺少必填值：{LABELS.get(k,k)}。')
    for k,v in out.items():
        if len(v)> (5000 if k=='allocation_refs' else 500 if k in ('note','allocation_evidence') else 100): raise DomainError('value_too_long', f'{k}字段过长。')
        if any(ord(c)<32 for c in v): raise DomainError('control_character','字段包含控制字符。')
    if out['account']!=options.account or out['currency']!=options.currency or out['direction']!=options.direction:
        raise DomainError('scope_mismatch', '记录账户、币种或方向与本批次范围不一致。')
    if out['status'] not in ('active','void'): raise DomainError('unknown_status','状态只支持 active/void，不能推断业务状态。')
    out['date']=day(out['date'])
    if not options.period_start<=out['date']<=options.period_end: raise DomainError('outside_period','记录日期不在声明范围内。')
    out['amount']=money(out['amount'],allow_zero=options.kind=='business')
    if options.kind=='business':
        if out['business_type'] not in ('redeem','renew','refund','other'): raise DomainError('business_type','业务类型尚未映射。')
        for k in ['principal','interest','fee']: out[k]=money(out[k])
        if sum(Decimal(out[k]) for k in ['principal','interest','fee'])!=Decimal(out['amount']):
            raise DomainError('component_mismatch','本金、利息和费用之和与明确应收付金额不一致；不得自动修改。')
    if options.kind == 'business' and (out['allocation_refs'] or out['allocation_evidence']):
        raise DomainError('allocation_wrong_side', '分配清单只能出现在资金流水侧。')
    if options.kind == 'fund' and out['allocation_refs']:
        refs = parse_allocation_refs(out['allocation_refs'])
        if out['event_ref']:
            raise DomainError('allocation_conflict', '合并分配与单一事件关联不可同时填写。')
        if not out['allocation_evidence']:
            raise DomainError('allocation_evidence_required', '合并收款必须提供明确的分配依据编号。')
        if sum(Decimal(x['amount']) for x in refs) != Decimal(out['amount']):
            raise DomainError('allocation_sum', '分配清单金额总和必须与资金流水金额完全一致。')
        out['allocation_refs'] = json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return out


def parse_allocation_refs(text: str) -> list[dict[str, str]]:
    """Only a source-provided allocation table, never an inferred subset sum."""
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        raise DomainError('invalid_allocation_refs', '分配清单须为JSON数组。')
    if not isinstance(raw, list) or not 2 <= len(raw) <= 20:
        raise DomainError('invalid_allocation_refs', '每笔合并收款须明确列出2至20项分配。')
    result = []; seen = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {'event_ref', 'amount'}:
            raise DomainError('invalid_allocation_refs', '每项只能包含 event_ref 和 amount。')
        ref = entry['event_ref']
        if not isinstance(ref, str) or not ref.strip() or len(ref)>100 or any(ord(c)<32 for c in ref):
            raise DomainError('invalid_allocation_refs', '分配事件标识无效。')
        ref = ref.strip()
        if ref in seen:
            raise DomainError('duplicate_allocation_ref', '同一清单不能重复列出同一事件。')
        if not isinstance(entry['amount'], str):
            raise DomainError('invalid_allocation_refs', '分配金额必须使用十进制字符串。')
        result.append({'event_ref': ref, 'amount': money(entry['amount'], allow_zero=False)})
        seen.add(ref)
    return sorted(result, key=lambda e: e['event_ref'])
