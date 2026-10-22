"""Resource-limited upload parser, used by preview before records or raw files persist.

No macros/formulas are executed. The worker gets no auth/model environment, no
runtime database path, and only the uploaded bytes. Resource limits are not a
complete hostile-file sandbox; filesystem/network confinement remains separate.
"""
from __future__ import annotations
import base64,json,os,signal,subprocess,sys,tempfile,threading
from pathlib import Path
from .domain import DomainError,MAX_BYTES,MAX_ROWS,MAX_COLUMNS

MAX_OUTPUT=32*1024*1024
WALL_SECONDS=15
SLOTS=threading.BoundedSemaphore(2)
WORKER=Path(__file__).with_name('parser_worker.py')

def worker_environment(home:Path)->dict:
    return {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(home),'TMPDIR':str(home),
            'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
            'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}

def _kill(process:subprocess.Popen)->None:
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    process.wait(timeout=5)

def parse_file_isolated(data:bytes,filename:str,*,sheet:str|None=None,header_row:int=1)->dict:
    if not data or len(data)>MAX_BYTES:raise DomainError('file_size','文件为空或超过8 MiB限制。')
    if not isinstance(filename,str) or len(filename)>255 or '\x00' in filename:raise DomainError('file_name','文件名无效。')
    if sheet is not None and (not isinstance(sheet,str) or len(sheet)>128):raise DomainError('sheet','工作表参数无效。')
    if type(header_row)is not int or not 1<=header_row<=30:raise DomainError('header_row','表头行无效。')
    if sys.platform!='linux':raise DomainError('parser_environment','本版隔离解析需要Linux，不自动退回进程内解析。',503)
    if not SLOTS.acquire(timeout=2):raise DomainError('parser_busy','解析队列已满，请稍后重试。',503)
    try:
        request=json.dumps({'data':base64.b64encode(data).decode('ascii'),'filename':Path(filename).name,
                            'sheet':sheet,'header_row':header_row},separators=(',',':')).encode()
        with tempfile.TemporaryDirectory(prefix='ddt-parser-') as folder:
            home=Path(folder);home.chmod(0o700)
            with tempfile.TemporaryFile(dir=home) as stdout,tempfile.TemporaryFile(dir=home) as stderr:
                p=subprocess.Popen([sys.executable,'-I','-u',str(WORKER)],stdin=subprocess.PIPE,
                    stdout=stdout,stderr=stderr,cwd=home,env=worker_environment(home),start_new_session=True)
                try:
                    p.communicate(input=request,timeout=WALL_SECONDS)
                except subprocess.TimeoutExpired:
                    _kill(p);raise DomainError('parse_timeout','文件解析超时，已停止解析进程。',422)
                finally:
                    # Also remove a child process that inherited the worker's process group.
                    if p.poll() is None:_kill(p)
                    else:
                        try:os.killpg(p.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                if p.returncode!=0:raise DomainError('parse_worker_failure','解析进程失败或超过资源限制，未保存该批次。')
                stdout.seek(0);raw=stdout.read(MAX_OUTPUT+1)
                if len(raw)>MAX_OUTPUT:raise DomainError('parse_output_limit','解析结果超过限制。')
                try:r=json.loads(raw)
                except (ValueError,UnicodeError):raise DomainError('parse_worker_failure','解析进程返回无效结果。')
        if not isinstance(r,dict) or type(r.get('ok'))is not bool:raise DomainError('parse_worker_failure','解析进程返回无效结构。')
        if not r['ok']:
            e=r.get('error',{})
            if not isinstance(e,dict) or not isinstance(e.get('code'),str) or not isinstance(e.get('message'),str) or type(e.get('status'))is not int:
                raise DomainError('parse_worker_failure','解析错误结果无效。')
            if len(e['code'])>80 or len(e['message'])>2000 or not 400<=e['status']<600:raise DomainError('parse_worker_failure','解析错误结果超限。')
            raise DomainError(e['code'],e['message'],e['status'])
        parsed=r.get('parsed')
        if (not isinstance(parsed,dict) or not isinstance(parsed.get('rows'),list) or len(parsed['rows'])>MAX_ROWS
            or not isinstance(parsed.get('headers'),list) or len(parsed['headers'])>MAX_COLUMNS):
            raise DomainError('parse_worker_failure','解析结果超出数据契约。')
        return parsed
    finally:SLOTS.release()
