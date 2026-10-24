"""Strict BIFF8 values reader and preflight for local offline testing.

Prefers xlrd when installed. The bounded fallback intentionally rejects SST
continuations, merged cells, formulas, macro sheets, encryption and unsupported
value records rather than silently dropping data. Not a general XLS replacement.
"""
from __future__ import annotations
import struct
from .domain import DomainError, MAX_ROWS, MAX_COLUMNS

END, FREE = 0xFFFFFFFE, 0xFFFFFFFF

def error(message='XLS结构损坏或包含本版不支持的特性。'):
    raise DomainError('unsupported_xls',message)

def u16(b,p=0): return struct.unpack_from('<H',b,p)[0]
def u32(b,p=0): return struct.unpack_from('<I',b,p)[0]

def workbook_stream(data: bytes) -> bytes:
    try:
        if len(data)<512 or data[:8]!=bytes.fromhex('d0cf11e0a1b11ae1'): error()
        major=u16(data,26); shift=u16(data,30); mini_shift=u16(data,32)
        if (major,shift) not in [(3,9),(4,12)] or mini_shift!=6 or u16(data,28)!=0xFFFE: error()
        size=1<<shift; sectors=(len(data)-size)//size
        def sector(sid):
            if sid>=sectors: error()
            return data[(sid+1)*size:(sid+2)*size]
        difat=[x for x in struct.unpack_from('<109I',data,76) if x!=FREE]
        nxt=u32(data,68); n=u32(data,72); seen=set()
        if n>32: error()
        for _ in range(n):
            if nxt in seen: error()
            seen.add(nxt); vals=struct.unpack('<'+'I'*(size//4),sector(nxt)); difat.extend(x for x in vals[:-1] if x!=FREE); nxt=vals[-1]
        if len(difat)!=u32(data,44) or len(set(difat))!=len(difat): error()
        fat=[]
        for sid in difat: fat.extend(struct.unpack('<'+'I'*(size//4),sector(sid)))
        def chain(start, table, getter, limit):
            chunks=[]; ids=set(); sid=start
            while sid!=END:
                if sid in ids or sid>=len(table) or len(ids)>limit: error()
                ids.add(sid); chunks.append(getter(sid)); sid=table[sid]
            return b''.join(chunks)
        directory=chain(u32(data,48),fat,sector,sectors)
        streams={}; root=None
        for p in range(0,len(directory)-127,128):
            ent=directory[p:p+128]; kind=ent[66]; nl=u16(ent,64)
            if not kind: continue
            if not 2<=nl<=64: error()
            name=ent[:nl-2].decode('utf-16le'); nlow=name.lower()
            if any(t in nlow for t in ['vba','objectpool','activex']): error('不接受XLS宏或嵌入对象。')
            length=struct.unpack_from('<Q',ent,120)[0] if major==4 else u32(ent,120)
            if length>len(data): error()
            spec=(u32(ent,116),length)
            if kind==5: root=spec
            if kind==2:
                if nlow in streams: error()
                streams[nlow]=spec
        spec=streams.get('workbook') or streams.get('book')
        if spec is None: error('OLE文件中没有Excel Workbook流。')
        start,length=spec
        if length<4096:
            if root is None: error()
            mini_root=chain(root[0],fat,sector,sectors)[:root[1]]
            mf=chain(u32(data,60),fat,sector,sectors)
            mini_fat=list(struct.unpack('<'+'I'*(len(mf)//4),mf))
            def mini(sid):
                if (sid+1)*64>len(mini_root): error()
                return mini_root[sid*64:(sid+1)*64]
            return chain(start,mini_fat,mini,len(mini_fat))[:length]
        return chain(start,fat,sector,sectors)[:length]
    except DomainError: raise
    except Exception: error()


def records(blob):
    p=0
    while p+4<=len(blob):
        typ,n=struct.unpack_from('<HH',blob,p)
        if typ==0 and n==0: break
        if n>8224 or p+4+n>len(blob): error()
        yield p,typ,blob[p+4:p+4+n]
        p+=4+n


def read_xls(data: bytes, selected: str|None) -> dict:
    blob=workbook_stream(data); allrec=list(records(blob))
    if not allrec or allrec[0][1]!=0x0809 or u16(allrec[0][2])!=0x0600: error('只支持BIFF8/Excel97-2003；旧版本须明确转换。')
    bounds=[]; sst=[]; has_continue=False
    for pos,t,b in allrec:
        if t in (0x0006,0x0206,0x0406,0x04BC,0x00BC): error('XLS含公式；要求静态导出值，不能使用缓存代替重算。')
        if t==0x002F: error('不读取密码保护XLS。')
        if t in (0x0023,0x0059): error('本版拒绝XLS外部名称或外部工作簿引用。')
        if t==0x0208 and len(b)>=16 and u32(b,12)&0x20: error('XLS含隐藏行。')
        if t==0x00E5: error('XLS含合并单元格；须明确模板展开规则。')
        if t==0x0085:
            if b[5]!=0 or b[4]!=0: error('不接受宏、隐藏或非普通工作表。')
            count=b[6]; uni=b[7]&1; name=b[8:8+count*(2 if uni else 1)].decode('utf-16le' if uni else 'latin1')
            bounds.append((u32(b),name))
        if t==0x003C: has_continue=True
    if not bounds: error('没有普通工作表。')
    names=[x[1] for x in bounds]
    if len(names)>1 and selected is None: raise DomainError('choose_sheet','XLS有多个工作表，请选择。')
    name=selected or names[0]
    if name not in names: raise DomainError('sheet_not_found','指定工作表不存在。')
    # Optional full reader is invoked only after preflight rejects active features.
    try: import xlrd
    except ImportError: xlrd=None
    if xlrd is not None:
        from .parsers import cell_text
        try:
            book=xlrd.open_workbook(file_contents=data,formatting_info=True)
            sh=book.sheet_by_name(name)
            if any(info.hidden for info in sh.rowinfo_map.values()): error('XLS含隐藏行。')
            if sh.nrows>MAX_ROWS+30 or sh.ncols>MAX_COLUMNS: error('XLS行列数超过限制。')
            out=[]; numeric=[]
            for rn in range(sh.nrows):
                vals=[]
                for cn in range(sh.ncols):
                    c=sh.cell(rn,cn)
                    if c.ctype==xlrd.XL_CELL_DATE: v=xlrd.xldate_as_datetime(c.value,book.datemode).date().isoformat()
                    elif c.ctype==xlrd.XL_CELL_ERROR: error('XLS含错误单元格。')
                    else:
                        v=cell_text(c.value)
                        if c.ctype==xlrd.XL_CELL_NUMBER:
                            numeric.append([rn+1,cn+1]); fmt=book.format_map[book.xf_list[c.xf_index].format_key].format_str
                            if len(fmt)>1 and set(fmt)=={'0'} and c.value.is_integer(): v=str(int(c.value)).zfill(len(fmt))
                    vals.append(v)
                out.append(vals)
            book.release_resources()
            return {'rows':out,'sheet':name,'sheets':names,'numeric_cells':numeric,'engine':'xlrd'}
        except DomainError: raise
        except Exception: error('xlrd无法解析此XLS。')
    if has_continue: error('离线精简XLS读取器不支持SST续块；安装requirements-xls.txt启用xlrd后重试。')
    for _,t,b in allrec:
        if t==0x00FC:
            total=u32(b,4)
            if total>MAX_ROWS*MAX_COLUMNS: error()
            p=8
            for _ in range(total):
                if p+3>len(b): error()
                count=u16(b,p); flags=b[p+2]; p+=3; runs=0; ext=0
                if flags&8: runs=u16(b,p);p+=2
                if flags&4: ext=u32(b,p);p+=4
                width=2 if flags&1 else 1
                end=p+count*width
                if end>len(b): error()
                sst.append(b[p:end].decode('utf-16le' if width==2 else 'latin1'));p=end+4*runs+ext
                if p>len(b): error()
    start=next(x[0] for x in bounds if x[1]==name)
    out={}; numeric=[]; maxr=-1; maxc=-1
    def put(r,c,v,isnum=False):
        nonlocal maxr,maxc
        if r>=MAX_ROWS+30 or c>=MAX_COLUMNS: error('XLS行列数超过限制。')
        if (r,c) in out: error('XLS有重复单元格。')
        out[r,c]=v; maxr=max(maxr,r);maxc=max(maxc,c)
        if isnum: numeric.append([r+1,c+1])
    from .parsers import cell_text
    try:
        for pos,t,b in allrec:
            if pos<start: continue
            if t==0x000A: break
            if t==0x00FD: put(u16(b),u16(b,2),sst[u32(b,6)])
            elif t==0x0203: put(u16(b),u16(b,2),cell_text(struct.unpack_from('<d',b,6)[0]),True)
            elif t==0x027E:
                rk=u32(b,6)
                v=(struct.unpack('<i',struct.pack('<I',rk))[0]>>2) if rk&2 else struct.unpack('<d',struct.pack('<II',0,rk&0xFFFFFFFC))[0]
                if rk&1: v/=100
                put(u16(b),u16(b,2),cell_text(v),True)
            elif t in (0x00BD,0x0204,0x0205,0x00D6,0x0002,0x0003,0x0004,0x0005,0x0207,0x0221): error('精简XLS读取器不支持该值记录；请安装xlrd后重试。')
            elif t in (0x0201,0x00BE): pass # blank cells
            elif t==0x0208 and len(b)>=16 and u32(b,12)&0x20: error('XLS含隐藏行。')
        rows=[[out.get((r,c),'') for c in range(maxc+1)] for r in range(maxr+1)]
        return {'rows':rows,'sheet':name,'sheets':names,'numeric_cells':numeric,'engine':'strict-biff8-fallback'}
    except DomainError: raise
    except Exception: error()
