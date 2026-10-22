from __future__ import annotations
import csv, io, json, os, re
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, Depends, File, Form, UploadFile, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .auth import Auth, Principal, load_users
from .database import Database
from .domain import *
from .service import Service, audit
from .assistant import TaskAssistant, TaskRequest, ExecuteRequest
from .vouchers import VoucherService, PolicyCreate, DecisionRequest, GenerateRequest, demo_policy

ROOT=Path(__file__).resolve().parents[2]

class BodyLimitMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        size=0;chunks=[]
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            size+=len(message.get('body',b''))
            if size>MAX_BYTES+1024*1024:
                return await JSONResponse({'error':{'code':'body_limit','message':'请求体超过原型限制。'}},status_code=413)(scope,receive,send)
            chunks.append(message)
            if not message.get('more_body',False):break
        async def replay():
            if chunks:return chunks.pop(0)
            return await receive()
        await self.app(scope,replay,send)


def safe_csv(value):
    text=str(value) if value is not None else ''
    if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')):return "'"+text
    return text


def create_app(db_path:Path|None=None,users:list[dict]|None=None,web_path:Path|None=None):
    state=Path(os.environ.get('DDT_STATE_DIR',str(ROOT/'state')))
    if users is None:users=load_users(state/'users.json')
    auth=Auth(users);database=Database(db_path or state/'app.sqlite3');svc=Service(database)
    app=FastAPI(title='典当会计辅助工作台',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.service=svc;app.state.db=database
    assistant=TaskAssistant(svc);vouchers=VoucherService(svc)
    app.state.assistant=assistant;app.state.vouchers=vouchers
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

    @app.middleware('http')
    async def secure_headers(request,call_next):
        response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
          'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):return JSONResponse({'error':{'code':exc.code,'message':exc.message}},status_code=exc.status)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse({'error':{'code':'invalid_request','message':'请求字段、类型或范围无效。','fields':[list(e['loc']) for e in exc.errors()]}},status_code=422)
    @app.get('/health')
    def health():return {'status':'ok','version':VERSION,'mode':'local-prototype'}
    @app.get('/api/schema')
    def schema(p:Principal=Depends(auth)):return app.openapi()
    @app.get('/api/meta')
    def meta(p:Principal=Depends(auth)):
        return {'version':VERSION,'user':{'id':p.user_id,'tenant':p.tenant,'role':p.role,'accounts':p.accounts},
          'external_api':'not_configured','llm':'not_connected','model_planners':assistant.planner.status(),'assistant':'bounded-read-only-workflow','database':'sqlite','mode':'synthetic-data-prototype',
          'rule_version':RULE_VERSION,'fields':{'business':BUSINESS,'fund':FUNDS,'optional':OPTIONAL,'labels':LABELS},
          'limits':{'file_bytes':MAX_BYTES,'rows':MAX_ROWS,'columns':MAX_COLUMNS},
          'warnings':['非典当通官方产品；真实导出未验收。','只能用于本地合成/脱敏演示，不是生产财务系统。']}
    @app.get('/api/batches')
    def batches(p:Principal=Depends(auth)):return svc.batches(p)
    @app.post('/api/batches/preview')
    def preview(file:Annotated[UploadFile,File()],options:Annotated[str,Form()],p:Principal=Depends(auth)):
        p.require('operator')
        try:opt=ImportOptions.model_validate_json(options)
        except Exception:raise DomainError('invalid_options','导入选项无效；须明确类型、账户、日期及字段映射。')
        data=file.file.read(MAX_BYTES+1)
        filename=(file.filename or 'unnamed').replace('\\','/').rsplit('/',1)[-1]
        filename=re.sub(r'[\x00-\x1f\x7f]','_',filename)[:160]
        return svc.create_batch(p,data,filename,opt)
    @app.get('/api/batches/{bid}')
    def batch(bid:str,p:Principal=Depends(auth)):return svc.batch_view(svc.batch(p,bid))
    @app.get('/api/batches/{bid}/rows')
    def batch_rows(bid:str,offset:int=0,limit:int=100,p:Principal=Depends(auth)):
        if offset<0 or not 1<=limit<=200:raise DomainError('pagination','分页范围无效。')
        b=svc.batch(p,bid);rows=json.loads(b['parsed'])['rows']
        return {'total':len(rows),'rows':rows[offset:offset+limit],'offset':offset,'limit':limit}
    @app.post('/api/batches/{bid}/confirm')
    def confirm(bid:str,req:ConfirmRequest,p:Principal=Depends(auth)):return svc.confirm(p,bid,req)
    @app.post('/api/reconcile')
    def reconcile(req:ReconcileRequest,p:Principal=Depends(auth)):return svc.reconcile(p,req)
    @app.get('/api/runs')
    def runs(p:Principal=Depends(auth)):return svc.runs(p)
    @app.get('/api/runs/{rid}')
    def run(rid:str,p:Principal=Depends(auth)):return svc.run_view(p,rid)
    @app.post('/api/proposals/{pid}/review')
    def review(pid:str,req:ReviewRequest,p:Principal=Depends(auth)):return svc.review(p,pid,req)
    @app.get('/api/audit')
    def history(p:Principal=Depends(auth)):return svc.audit_events(p)
    @app.get('/api/runs/{rid}/export')
    def export(rid:str,format:str='csv',p:Principal=Depends(auth)):
        run=svc.run_view(p,rid)
        if format not in ('csv','json','funds_csv'):raise DomainError('export_format','仅支持CSV或JSON辅助导出。')
        if format=='json':body=json.dumps({'purpose':'辅助核对，非正式记账','run':run},ensure_ascii=False,indent=2).encode();media='application/json'
        elif format=='funds_csv':
            stream=io.StringIO(newline='');writer=csv.writer(stream)
            writer.writerow(['用途','任务编号','任务是否过期','流水编号','账户','方向','日期','金额','已认领','未认领','明确关联事件','来源'])
            for item in run['fund_balances']:
                rec=item['record'];d=rec['data']
                origin='; '.join(f"{s['filename']}|{s['sheet']}|行{s['row_number']}|SHA256:{s['sha256']}" for s in rec['sources'])
                writer.writerow([safe_csv(v) for v in ['辅助核对，非正式记账',rid,str(run['stale']),d['record_id'],d['account'],d['direction'],d['date'],d['amount'],item['allocated'],item['remaining'],d['event_ref'],origin]])
            body=('\ufeff'+stream.getvalue()).encode();media='text/csv; charset=utf-8'
        else:
            stream=io.StringIO(newline='');writer=csv.writer(stream)
            writer.writerow(['用途','任务编号','源快照','规则版本','任务是否过期','业务事件','当票号','业务日期','应收金额','流水编号','本事件分配金额','分类','审核状态','说明','业务来源','资金来源'])
            for pr in run['proposals']:
                def source_string(record):return '; '.join(f"{s['filename']}|{s['sheet']}|行{s['row_number']}|SHA256:{s['sha256']}" for s in record['sources'])
                b=pr['business'];funds=pr['funds']
                writer.writerow([safe_csv(v) for v in ['辅助核对，非正式记账',rid,run['snapshot'],run['rule_version'],str(run['stale']),b['data']['record_id'],b['data']['ticket_id'],b['data']['date'],b['data']['amount'],
                  ';'.join(f['data']['record_id'] for f in funds),pr['proposed_amount'],pr['category'],pr['status'],pr['reason'],source_string(b),'; '.join(source_string(f) for f in funds)]])
            body=('\ufeff'+stream.getvalue()).encode();media='text/csv; charset=utf-8'
        with database.transaction() as c:audit(c,p,run['account'],'result.export',rid,{'format':format,'stale':run['stale']})
        return Response(body,media_type=media,headers={'Content-Disposition':f'attachment; filename="reconciliation-{rid}.{"json" if format=="json" else "csv"}"'})
    @app.get('/api/samples/{name}')
    def sample(name:str,p:Principal=Depends(auth)):
        if p.tenant!='DEMO':raise DomainError('forbidden','仅DEMO空间提供内置合成样例。',403)
        if name not in ('business.csv','funds.csv','business.xls','funds.xls','business.xlsx','funds.xlsx','business_invalid.csv','business_correction.csv','combined_business.csv','combined_funds.csv'):
            raise DomainError('not_found','样例不存在。',404)
        return FileResponse(ROOT/'samples'/name,filename=name)
    @app.get('/api/integrations/diandang')
    def integration(p:Principal=Depends(auth)):
        return {'status':'not_configured','message':'尚未取得受支持的厂商API文档与授权；本接口不返回模拟业务数据。'}
    @app.get('/api/assistant/status')
    def assistant_status(p:Principal=Depends(auth)):return assistant.planner.status()
    @app.get('/api/assistant/tasks')
    def assistant_tasks(p:Principal=Depends(auth)):return assistant.list(p)
    @app.post('/api/assistant/tasks')
    def create_task(req:TaskRequest,p:Principal=Depends(auth)):return assistant.create(p,req)
    @app.get('/api/assistant/tasks/{tid}')
    def read_task(tid:str,p:Principal=Depends(auth)):return assistant.get(p,tid)
    @app.post('/api/assistant/tasks/{tid}/execute')
    def execute_task(tid:str,req:ExecuteRequest,p:Principal=Depends(auth)):return assistant.execute(p,tid,req)
    @app.get('/api/voucher-policies')
    def policy_list(p:Principal=Depends(auth)):return vouchers.policies(p)
    @app.post('/api/voucher-policies')
    def policy_create(req:PolicyCreate,p:Principal=Depends(auth)):return vouchers.create_policy(p,req)
    @app.get('/api/voucher-policies/demo-template')
    def policy_demo(account:str='TEST-001',namespace:str='combined-demo-v1',p:Principal=Depends(auth)):
        p.account(account)
        if p.tenant!='DEMO':raise DomainError('forbidden','仅DEMO空间提供演示政策。',403)
        return demo_policy(account,namespace)
    @app.post('/api/voucher-policies/{pid}/review')
    def policy_decision(pid:str,req:DecisionRequest,p:Principal=Depends(auth)):return vouchers.decide_policy(p,pid,req)
    @app.get('/api/vouchers')
    def voucher_list(run_id:str|None=None,p:Principal=Depends(auth)):return vouchers.list(p,run_id)
    @app.post('/api/vouchers/generate')
    def voucher_generate(req:GenerateRequest,p:Principal=Depends(auth)):return vouchers.generate(p,req)
    @app.get('/api/vouchers/{vid}')
    def voucher_read(vid:str,p:Principal=Depends(auth)):return vouchers.get(p,vid)
    @app.post('/api/vouchers/{vid}/review')
    def voucher_review(vid:str,req:DecisionRequest,p:Principal=Depends(auth)):return vouchers.decide(p,vid,req)
    @app.get('/api/vouchers/{vid}/export')
    def voucher_export(vid:str,format:str='json',p:Principal=Depends(auth)):
        body,media=vouchers.export(p,vid,format)
        return Response(body,media_type=media,headers={'Content-Disposition':f'attachment; filename="voucher-{vid}.{format}"'})
    static=web_path or ROOT/'web' 
    if static.is_dir():app.mount('/',StaticFiles(directory=static,html=True),name='workbench')
    return app
