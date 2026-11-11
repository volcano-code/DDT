"""Public fixtures are readable-data generated, deterministic and non-overwriting."""
import hashlib
from pathlib import Path
import pytest
from generate_public_fixtures import generated_files, materialize
from synthetic_xls import write_fixture
from app.parsers import parse_file
from sample_data import BUSINESS, FUNDS


def test_fixture_generation_repeat_bytes():
    assert generated_files() == generated_files()


def test_materialize_repeat_preserves_bytes(tmp_path):
    dest=tmp_path/'samples'
    assert materialize(dest)==12
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir()}
    assert materialize(dest)==12
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir()}


def test_existing_different_sample_is_not_overwritten(tmp_path):
    dest=tmp_path/'samples';dest.mkdir();(dest/'business.csv').write_text('KEEP')
    with pytest.raises(ValueError):materialize(dest)
    assert (dest/'business.csv').read_text()=='KEEP'
    assert len(list(dest.iterdir()))==1


def test_symlink_directory_refused(tmp_path):
    real=tmp_path/'real';real.mkdir();link=tmp_path/'link';link.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError):materialize(link)
    assert not list(real.iterdir())


def test_symlink_file_refused(tmp_path):
    dest=tmp_path/'samples';dest.mkdir();other=tmp_path/'other';other.write_text('KEEP')
    (dest/'funds.csv').symlink_to(other)
    with pytest.raises(ValueError):materialize(dest)
    assert other.read_text()=='KEEP'


def test_synthetic_formats_same_explicit_records():
    files=generated_files()
    for name,expected in [('business',BUSINESS),('funds',FUNDS)]:
        for ext in ['csv','xls','xlsx']:
            parsed=parse_file(files[name+'.'+ext],name+'.'+ext)
            assert [r['values'] for r in parsed['rows']]==expected


def test_small_xls_writer_rejects_general_export_scope():
    for rows in [[],[['h']]*33,[['h'],['x','y']],[['h'],[123]],[['h'],['长'*501]]]:
        with pytest.raises(ValueError):write_fixture(rows)
