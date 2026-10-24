"""Small BIFF8/CFB test-fixture writer, not a production spreadsheet exporter.

Only one visible worksheet and static string cells; no formulas, macros, links,
mini-streams or SST continuation. Bounded to the public synthetic test dataset.
"""
from __future__ import annotations
import struct

END, FREE, FAT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD


def record(kind: int, payload: bytes = b'') -> bytes:
    if len(payload) > 8224:
        raise ValueError('Fixture record exceeds the supported BIFF8 limit')
    return struct.pack('<HH', kind, len(payload)) + payload


def bof(kind: int) -> bytes:
    return record(0x0809, struct.pack('<HHHHII', 0x0600, kind, 0x0DBB, 1996, 0x41, 0x06))


def directory_entry(name: str, kind: int, start: int, size: int, child: int = FREE) -> bytes:
    raw = bytearray(128)
    label = (name + '\0').encode('utf-16le')
    if len(label) > 64:
        raise ValueError('Fixture directory name too long')
    raw[:len(label)] = label
    struct.pack_into('<HBBIII', raw, 64, len(label), kind, 1, FREE, FREE, child)
    struct.pack_into('<IQ', raw, 116, start, size)
    return bytes(raw)


def write_fixture(rows: list[list[str]]) -> bytes:
    if not rows or len(rows) > 32 or not rows[0] or len(rows[0]) > 32:
        raise ValueError('This writer accepts only small synthetic fixtures')
    width = len(rows[0])
    values, indexes = [], {}
    cells = []
    for rn, row in enumerate(rows):
        if len(row) != width:
            raise ValueError('Fixture rows must have equal lengths')
        for cn, value in enumerate(row):
            if not isinstance(value, str) or len(value.encode('utf-16le')) > 1000:
                raise ValueError('Only short static strings are accepted')
            if value not in indexes:
                indexes[value] = len(values); values.append(value)
            cells.append(record(0x00FD, struct.pack('<HHHI', rn, cn, 0, indexes[value])))
    strings = b''
    for value in values:
        encoded = value.encode('utf-16le')
        strings += struct.pack('<HB', len(encoded)//2, 1) + encoded
    sst = record(0x00FC, struct.pack('<II', len(cells), len(values)) + strings)
    # Default FONT and cell XF; string values never carry formula records.
    font = record(0x0031, struct.pack('<HHHHHBBBB', 200, 0, 0x7FFF, 400, 0, 0, 0, 0, 0)
                  + bytes([5, 0]) + b'Arial')
    xf = record(0x00E0, bytes(20))
    prefix = bof(0x0005) + record(0x0042, struct.pack('<H',1200)) + font + xf
    bound_payload = struct.pack('<IBBBB', 0, 0, 0, 4, 0) + b'Data'
    globals_length = len(prefix) + len(record(0x0085, bound_payload)) + len(sst) + 4
    bounds = record(0x0085, struct.pack('<IBBBB', globals_length, 0, 0, 4, 0) + b'Data')
    workbook = (prefix + bounds + sst + record(0x000A) + bof(0x0010)
                + record(0x0081, bytes(2))
                + record(0x0200, struct.pack('<IIHHH',0,len(rows),0,width,0))
                + b''.join(cells) + record(0x023E, struct.pack('<HHHIHHI',0x06B6,0,0,0x40,0,0,0))
                + record(0x000A))
    # Padding above the mini-stream cutoff deliberately avoids a second allocator.
    length = max(4096, ((len(workbook)+511)//512)*512)
    workbook += bytes(length-len(workbook))
    n = length//512
    if n + 2 > 128:
        raise ValueError('Fixture exceeds the single-FAT-sector limit')
    header = bytearray(512)
    header[:8] = bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<HHHHH',header,24,0x003E,3,0xFFFE,9,6)
    struct.pack_into('<IIIIIIIII',header,40,0,1,0,0,4096,END,0,END,0)
    struct.pack_into('<109I',header,76,n+1,*([FREE]*108))
    directory = (directory_entry('Root Entry',5,END,0,1)
                 + directory_entry('Workbook',2,1,length) + bytes(256))
    table = [END] + [i+1 for i in range(1,n)] + [END,FAT]
    table += [FREE]*(128-len(table))
    return bytes(header) + directory + workbook + struct.pack('<128I',*table)
