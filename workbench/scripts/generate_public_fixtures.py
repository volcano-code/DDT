#!/usr/bin/env python3
"""Create bounded synthetic CSV/XLSX/BIFF8 fixtures from readable rows.

No encoded binary payload, vendor code, customer data or network requests.
Differing existing files are refused; repeat runs are byte-identical.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile
from datetime import datetime
from openpyxl import Workbook
from sample_data import BUSINESS, FUNDS, BUS_HEADERS, FUND_HEADERS, csv_bytes
ROOT = Path(__file__).resolve().parents[1]
from synthetic_xls import write_fixture


def original_biff(name: str) -> bytes:
    # Name kept for test compatibility; output now comes from readable rows.
    if name == 'business.xls': rows, headers = BUSINESS, BUS_HEADERS
    elif name == 'funds.xls': rows, headers = FUNDS, FUND_HEADERS
    else: raise ValueError('Unknown synthetic workbook')
    return write_fixture([headers] + [[row.get(h, '') for h in headers] for row in rows])


def xlsx(rows: list[dict], headers: list[str]) -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = 'Data'
    wb.properties.creator = wb.properties.lastModifiedBy = 'DDT synthetic fixture generator'
    wb.properties.created = wb.properties.modified = datetime(2026, 9, 29)
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, '') for h in headers])
    for row in ws:
        for cell in row:
            if cell.data_type == 'f':
                raise ValueError('Formulas are not synthetic fixture values')
            cell.number_format = '@'
    temporary = io.BytesIO(); wb.save(temporary); wb.close()
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(temporary.getvalue())) as src, zipfile.ZipFile(result, 'w') as dst:
        for name in sorted(src.namelist()):
            data = src.read(name)
            if name == 'docProps/core.xml':
                data = re.sub(rb'(<dcterms:modified[^>]*>).*?(</dcterms:modified>)',
                              rb'\g<1>2026-09-29T00:00:00Z\g<2>', data)
            item = zipfile.ZipInfo(name, (2026, 9, 29, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED; item.external_attr = 0o600 << 16
            dst.writestr(item, data)
    return result.getvalue()


def generated_files() -> dict[str, bytes]:
    result = {}
    for name, rows, headers in [('business', BUSINESS, BUS_HEADERS), ('funds', FUNDS, FUND_HEADERS)]:
        result[name + '.csv'] = csv_bytes(rows, headers)
        result[name + '.json'] = json.dumps(rows, ensure_ascii=False, indent=2).encode()
        result[name + '.xls'] = original_biff(name + '.xls')
        result[name + '.xlsx'] = xlsx(rows, headers)
    result['business_invalid.csv'] = csv_bytes([
        dict(BUSINESS[0], record_id='BAD-001', amount=''),
        dict(BUSINESS[1], record_id='BAD-002', amount='NaN')], BUS_HEADERS)
    result['business_correction.csv'] = csv_bytes([dict(BUSINESS[0], amount='10610.00', fee='110.00')], BUS_HEADERS)
    business = [dict(BUSINESS[0], record_id='C001', ticket_id='000201', amount='6000.00',
                     principal='5800.00', interest='150.00', fee='50.00', counterparty_ref='MASK-C'),
                dict(BUSINESS[0], record_id='C002', ticket_id='000202', amount='4000.00',
                     principal='3900.00', interest='80.00', fee='20.00', counterparty_ref='MASK-C')]
    funds = [dict(FUNDS[0], record_id='CF001', amount='10000.00', event_ref='', counterparty_ref='MASK-C',
                  allocation_refs=json.dumps([{'event_ref':'C001','amount':'6000.00'},
                                               {'event_ref':'C002','amount':'4000.00'}]),
                  allocation_evidence='SYNTHETIC-ALLOCATION-001')]
    result['combined_business.csv'] = csv_bytes(business, BUS_HEADERS)
    result['combined_funds.csv'] = csv_bytes(funds, FUND_HEADERS + ['allocation_refs', 'allocation_evidence'])
    return result


def materialize(folder: Path) -> int:
    folder = folder.absolute()
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise ValueError('Symlink destination refused')
    files = generated_files()
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = folder / name
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes() != data)):
            raise ValueError('Refusing to replace differing fixture: ' + name)
    for name, data in files.items():
        target = folder / name
        if target.exists():
            continue
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.synthetic-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temp, target)  # Refuse overwrite, including a concurrent writer.
        finally:
            temp.unlink(missing_ok=True)
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'samples')
    args = parser.parse_args()
    print(json.dumps({'status':'ready', 'files':materialize(args.output), 'synthetic_only':True}))
