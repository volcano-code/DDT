import io,zipfile,json,struct
from pathlib import Path
import pytest
from app.domain import DomainError,money
from app.parsers import parse_file
from app.main import safe_csv
from conftest import ROOT

@pytest.mark.parametrize('value',['','NaN','Infinity','1e3','-1','1,000','1.001','+1',None,'00.10','  ','9999999999999'])
def test_money_rejects_ambiguous_or_invalid_values(value):
    with pytest.raises(DomainError):money(value)

@pytest.mark.parametrize('value,expected',[('0','0.00'),('0.10','0.10'),('100.5','100.50'),('100','100.00')])
def test_money_decimal_scale(value,expected):assert money(value)==expected

@pytest.mark.parametrize('name',['business','funds'])
@pytest.mark.parametrize('ext',['csv','xls','xlsx'])
def test_genuine_format_samples(name,ext):
    p=ROOT/'samples'/f'{name}.{ext}';parsed=parse_file(p.read_bytes(),p.name)
    assert len(parsed['rows'])==9
    canonical=json.loads((ROOT/'samples'/f'{name}.json').read_text())
    assert [r['values'] for r in parsed['rows']]==canonical
    if ext=='xls':assert p.read_bytes()[:8]==bytes.fromhex('d0cf11e0a1b11ae1')
    if name=='business':assert parsed['rows'][0]['values']['ticket_id']=='000001'

@pytest.mark.parametrize('name,data',[('fake.xls',b'id,amount\na,1'),('bad.csv',b'xx\x00xx'),('fake.xlsx',b'not excel'),('a.exe',b'MZabcdef'),('export.csv',b'<html>report</html>')])
def test_format_spoofing_rejected(name,data):
    with pytest.raises(DomainError):parse_file(data,name)

def mutate_xlsx(name,transform=None,extra=None):
    data=(ROOT/'samples'/'business.xlsx').read_bytes();s=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src,zipfile.ZipFile(s,'w',zipfile.ZIP_DEFLATED) as dst:
        for n in src.namelist():
            b=src.read(n)
            if n==name and transform:b=transform(b)
            dst.writestr(n,b)
        if extra:dst.writestr(*extra)
    return s.getvalue()

def test_xlsx_formula_rejected():
    import xml.etree.ElementTree as ET
    def inject(blob):
        ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        root=ET.fromstring(blob)
        cell=root.find('.//{'+ns+'}c[@r="F2"]')
        assert cell is not None
        cell.clear();cell.set('r','F2')
        ET.SubElement(cell,'{'+ns+'}f').text='1+1'
        ET.SubElement(cell,'{'+ns+'}v').text='2'
        return ET.tostring(root)
    data=mutate_xlsx('xl/worksheets/sheet1.xml',inject)
    with pytest.raises(DomainError,match='公式'):parse_file(data,'formula.xlsx')

def test_xlsx_external_links_rejected():
    data=mutate_xlsx('',extra=('xl/externalLinks/externalLink1.xml','<x/>'))
    with pytest.raises(DomainError,match='外部链接'):parse_file(data,'external.xlsx')

def test_xlsx_macro_rejected():
    data=mutate_xlsx('',extra=('xl/vbaProject.bin',b'not executed'))
    with pytest.raises(DomainError,match='宏'):parse_file(data,'macro.xlsx')

def test_xlsx_zip_bomb_rejected():
    data=mutate_xlsx('',extra=('bomb.txt','0'*1000000))
    with pytest.raises(DomainError,match='压缩'):parse_file(data,'bomb.xlsx')

def test_corrupt_xls_rejected():
    data=bytearray((ROOT/'samples'/'business.xls').read_bytes());data[48:52]=struct.pack('<I',99999)
    with pytest.raises(DomainError):parse_file(bytes(data),'corrupt.xls')

def test_duplicate_header_rejected():
    with pytest.raises(DomainError,match='重复'):parse_file(b'id,id\na,b\n','duplicate.csv')

def test_blank_rows_are_accounted_for():
    d=parse_file(b'id,value\na,1\n\nb,2\n','blank.csv');assert d['blank_rows']==[3];assert len(d['rows'])==2

def test_extra_data_column_rejected():
    with pytest.raises(DomainError):parse_file(b'id,value\na,1,EXTRA\n','extra.csv')

@pytest.mark.parametrize('s',['=SUM(A1:A2)','+cmd','-cmd','@x','   =x','\t=1'])
def test_csv_formula_injection_escaped(s):assert safe_csv(s).startswith("'")

def test_literal_number_export_unchanged():assert safe_csv('100.00')=='100.00'

@pytest.mark.parametrize('header_row',[0,-1,31])
def test_header_row_range(header_row):
    with pytest.raises(DomainError):
        parse_file(b'a,b\n1,2\n','input.csv',header_row=header_row)

def test_corrupt_xls_parser_exception_is_sanitized(monkeypatch):
    import app.xls_safe as module
    def broken(*a,**kw):raise IndexError('confidential untrusted content')
    monkeypatch.setattr(module,'read_xls',broken)
    with pytest.raises(DomainError) as e:parse_file(bytes.fromhex('d0cf11e0a1b11ae1')+b'0'*512,'bad.xls')
    assert e.value.code=='invalid_xls'
    assert 'confidential' not in str(e.value)

@pytest.mark.parametrize('reason',['  ','\t\t','\n\n'])
def test_review_reason_not_whitespace(reason):
    from app.domain import ReviewRequest
    with pytest.raises(ValueError):ReviewRequest(decision='approve',expected_version=1,idempotency_key='some-unique-key',reason=reason)
