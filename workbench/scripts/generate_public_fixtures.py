#!/usr/bin/env python3
"""Materialize bounded SYNTHETIC fixtures, not software or customer data.

The two small BIFF8 fixtures were created with Apache POI for the original
synthetic dataset. Encodings are test data only, not executable JAR/EXE/archives.
Their exact digests are checked before any file is written. XLSX is generated
from the same explicit rows with openpyxl; no macro, external link or formula.
Existing differing files are never overwritten. No network or model calls.
"""
from __future__ import annotations
import base64, csv, hashlib, io, json, os, sys, zipfile, zlib
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from sample_data import BUSINESS,FUNDS,BUS_HEADERS,FUND_HEADERS,csv_bytes
ROOT=Path(__file__).resolve().parents[1]
BIFF = {
  "business.xls": {
    "sha256": "10857a863caee0551035db7d3c167285f3385964d719c9e0f1979913e252b6b2",
    "bytes": 6656,
    "zlib_base64": "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"
  },
  "funds.xls": {
    "sha256": "3b7099ae7882a9e48078171bca45548462b730a2a54d28a4fb42b0acb0685810",
    "bytes": 6144,
    "zlib_base64": "eNrtmF1sFFUUx898z5R+bEsBKV9lC9KPLWx322UL0gJag0G+SokS0XVp17Zp2cXttrHxwVUhmiCJiT4Q9YVHXxQTow8aIw8mPpD4FTUxMQETffPBRBMfkPWcM7O39y6LoZGQKNzNzJ3/mXPu/c2d+7Xz5ReNl8+/13IFKtJ2MOBayQNbsmmyQwjAC2zXSqUSmWrwKN1N/6k0DDn8FaAVhiCLeR7mYTFpOVhauSztJmO0oP9QegRrz8MUHGeOKVhsagJdk5/nZuO+a4BbkvSKPg+3Of3b+j0XX4RtwTehS84u1DTer2CpF8yLXPZPeByDk2AC5HO5QuvtSruZIa0Rww4+v4WWeljJVE18Xsrnd/nux74n3pnQ6rVj2z5pSwZ97VF9J/ud5XOYz/V41uBDjvmBLT2wDD6ntnzu1aAbW7ArP5mevoU31poteLkMITaE2yJtbdFUx/b28nXHWnMdtn6LJcvex4DsG+yyrlzb6AS+VETbuGXSyCgDOjIpqRSYWjwro7K03+QwFuDmrps1MUYNBsJ1ViE1QYS+33a8MM2zQqLkmhbvMg6JQFxoRbZgW2QgjWl4CkVtR2H5jVbWo2FWIr6lFoHsMcyNNFhgQDeoCTn2DJnkKJfRxwAHMFmS8b9tFw35ndWC9sMnYHaeLI6Rj8IX5Yq33+HPOK2fdxvfFEc/vs0Qu0eS3pMXi0u9z5A/Nv2aFCfSy1C+5Q2gwHccMei9HbuxsO7RzWi5HBhQ2GxSPrfPD+CT+ifdNPIPjmB3X/SmdxmxjlFMHZGJlMsxvaKGoaVRmxrhMM00zxmSSbrXZQgz/Db3S7JcLXVhOqcKrJCMFg0opJIYzNxFZvXVdIuJBRKPvsWAcWaGdL08vh18zPMPsHsVaRrVafmQR44HnR0Bo1w+/cZ4sFcdg15WhREqdCGCZhpfXeig3cprYwYeksc0Fw95G84C3Y2cP2UodX7iY+yaqIeStNjyUxOhCVr+xHq7j9RzmrJ17228ldibEZpLRk1haI+OaLoWZsQZxaJpE9g8R8+OF7ZiFtXMm+gdzaA8byl6Nsl0LopSkCkKG9ZQu3Rh6h9N7xbgqfzONHFsew0hvRBFb+NxlGGdknwZGQ9unrQC8FL33PLRWK63RIp4T3gm/zB6GUP9zEcO8iOHa3QQD+WtJ/B6WxHG/gKxkN/HFdKjhxvY19YmP3sLn/8fRgC1U8l0tUFZbqPkOHhxSFtNllWB0ajwO3jVwCU3EIdaSHrtkrDHGnqg0XauDaSCS+5Hxc7aFZdINduW7Am46pIuEXBx6EjwIfy+D4JuFnZ64je5p/d+b/G42xf+z/R28z5H2xHSoI="
  }
}

def original_biff(name):
    item=BIFF[name]
    compressed=base64.b64decode(item['zlib_base64'],validate=True)
    decoder=zlib.decompressobj()
    data=decoder.decompress(compressed,item['bytes']+1)
    if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise ValueError('Synthetic BIFF fixture expansion failed')
    if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
        raise ValueError('Synthetic BIFF fixture digest mismatch')
    return data

def xlsx(rows,headers):
    wb=Workbook();ws=wb.active;ws.title='Data'
    wb.properties.creator='DDT synthetic fixture generator'
    wb.properties.lastModifiedBy='DDT synthetic fixture generator'
    wb.properties.created=wb.properties.modified=datetime(2026,9,29)
    ws.append(headers)
    for row in rows:ws.append([row.get(h,'') for h in headers])
    for row in ws.iter_rows():
        for cell in row:
            if cell.data_type=='f':raise ValueError('Synthetic fixture must contain no formulas')
            cell.number_format='@'
    temporary=io.BytesIO();wb.save(temporary);wb.close()
    result=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(temporary.getvalue())) as src,zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            entry=zipfile.ZipInfo(name,(2026,9,29,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o600<<16
            dst.writestr(entry,src.read(name))
    return result.getvalue()

def generated_files():
    result={}
    for name,rows,headers in (('business',BUSINESS,BUS_HEADERS),('funds',FUNDS,FUND_HEADERS)):
        result[name+'.csv']=csv_bytes(rows,headers)
        result[name+'.json']=json.dumps(rows,ensure_ascii=False,indent=2).encode()
        result[name+'.tsv']=('\n'.join(['\t'.join(headers)]+['\t'.join(r.get(h,'') for h in headers) for r in rows])+'\n').encode()
        result[name+'.xls']=original_biff(name+'.xls')
        result[name+'.xlsx']=xlsx(rows,headers)
    result['business_invalid.csv']=csv_bytes([dict(BUSINESS[0],record_id='BAD-001',amount=''),dict(BUSINESS[1],record_id='BAD-002',amount='NaN')],BUS_HEADERS)
    result['business_correction.csv']=csv_bytes([dict(BUSINESS[0],amount='10610.00',fee='110.00')],BUS_HEADERS)
    combined=[dict(BUSINESS[0],record_id='C001',ticket_id='000201',amount='6000.00',principal='5800.00',interest='150.00',fee='50.00',counterparty_ref='MASK-C'),dict(BUSINESS[0],record_id='C002',ticket_id='000202',amount='4000.00',principal='3900.00',interest='80.00',fee='20.00',counterparty_ref='MASK-C')]
    funds=[dict(FUNDS[0],record_id='CF001',amount='10000.00',event_ref='',counterparty_ref='MASK-C',allocation_refs=json.dumps([{'event_ref':'C001','amount':'6000.00'},{'event_ref':'C002','amount':'4000.00'}]),allocation_evidence='SYNTHETIC-ALLOCATION-001')]
    result['combined_business.csv']=csv_bytes(combined,BUS_HEADERS)
    result['combined_funds.csv']=csv_bytes(funds,FUND_HEADERS+['allocation_refs','allocation_evidence'])
    return result

def main():
    folder=ROOT/'samples'
    if folder.is_symlink() or any(p.is_symlink() for p in folder.parents):raise ValueError('Unsafe sample directory')
    files=generated_files()
    folder.mkdir(exist_ok=True)
    # Validate the whole destination before publishing; no unrelated data touched.
    for name,raw in files.items():
        target=folder/name
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes()!=raw)):
            raise ValueError('Refusing to replace existing different sample: '+name)
    for name,raw in files.items():
        target=folder/name
        if not target.exists():
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw)
    print(json.dumps({'status':'synthetic_fixtures_ready','files':len(files),'no_customer_or_vendor_binary':True,'bytes':sum(map(len,files.values()))}))

if __name__=='__main__':main()
