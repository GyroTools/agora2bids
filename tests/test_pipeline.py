"""End-to-end run with a fake Agora source, synthetic DICOM and the real dcm2niix binary."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pydicom
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from agora2bids.agora_source import Candidate, PatientInfo
from agora2bids.pipeline import run

try:
    from agora2bids.convert import find_dcm2niix

    find_dcm2niix()
    HAVE_DCM2NIIX = True
except FileNotFoundError:
    HAVE_DCM2NIIX = False

pytestmark = pytest.mark.skipif(not HAVE_DCM2NIIX, reason="dcm2niix not available")


def write_series(directory: Path, technique, tr, te, ti=None, description="scan", n=4, seq="GR"):
    directory.mkdir(parents=True, exist_ok=True)
    study, series = generate_uid(), generate_uid()
    rng = np.random.default_rng(0)
    for i in range(n):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
        ds.Modality, ds.PatientName, ds.PatientID = "MR", "Test^Patient", "1"
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.SeriesNumber, ds.InstanceNumber = study, series, 1, i + 1
        ds.SeriesDescription, ds.ProtocolName = description, description
        ds.ImageType = ["ORIGINAL", "PRIMARY", "M", "FFE"]
        ds.Rows = ds.Columns = 16
        ds.PixelSpacing, ds.SliceThickness = [1.0, 1.0], 1.0
        ds.ImagePositionPatient, ds.ImageOrientationPatient = [0.0, 0.0, float(i)], [1, 0, 0, 0, 1, 0]
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit, ds.PixelRepresentation = 15, 0
        ds.ScanningSequence, ds.SequenceVariant, ds.MRAcquisitionType = seq, "SP", "3D"
        ds.RepetitionTime, ds.EchoTime, ds.FlipAngle = tr, te, 15
        if ti:
            ds.InversionTime = ti
        ds.add_new((0x0018, 0x9005), "SH", technique)
        ds.PixelData = rng.integers(0, 1000, (16, 16), dtype=np.uint16).tobytes()
        ds.save_as(directory / f"IM_{i:04d}.dcm", enforce_file_format=True)


class FakeSource:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.files = {}
        write_series(tmp_path / "s_t1", "T1TFE", 8.0, 3.6, description="t1 tfe")
        write_series(tmp_path / "s_flair", "TIR", 6000.0, 120.0, ti=2400.0, description="flair", seq="IR")
        write_series(tmp_path / "s_survey", "T1TFE", 11.0, 4.6, description="Survey")
        self.series = [
            SimpleNamespace(id=1, name="t1 tfe", acquisition_number=1, dataset_types=[300], is_refscan=False, is_coil_survey=False, src="s_t1"),
            SimpleNamespace(id=2, name="flair", acquisition_number=2, dataset_types=[300], is_refscan=False, is_coil_survey=False, src="s_flair"),
            SimpleNamespace(id=3, name="Survey", acquisition_number=3, dataset_types=[300], is_refscan=False, is_coil_survey=False, src="s_survey"),
        ]

    def get_exam(self, exam_id):
        return SimpleNamespace(id=exam_id, patient=5, start_time="2025-01-10T09:00:00Z")

    def patient(self, exam):
        return PatientInfo(id=5, sex="f", birth_date="1980-01-10")

    def candidates(self, exam):
        return [Candidate(s, SimpleNamespace(id=100 + s.id, src=s.src), "dicom", i) for i, s in enumerate(self.series)]

    def parameters(self, dataset):
        return None  # no GOAL/DB parameters: classify from the DICOM tags

    def download(self, dataset, target: Path):
        shutil.copytree(self.tmp / dataset.src, target)
        return target


def test_exam_to_bids_end_to_end(tmp_path):
    source = FakeSource(tmp_path / "in")
    out = tmp_path / "bids"
    result = run(77, out, source)

    status = {e.series: e.status for e in result.entries}
    assert status == {"t1 tfe": "converted", "flair": "converted", "Survey": "excluded"}
    anat = out / "sub-5" / "ses-77" / "anat"
    assert (anat / "sub-5_ses-77_T1w.nii.gz").exists() and (anat / "sub-5_ses-77_T1w.json").exists()
    assert (anat / "sub-5_ses-77_FLAIR.nii.gz").exists()
    assert json.loads((out / "dataset_description.json").read_text())["DatasetType"] == "raw"
    assert (out / "participants.tsv").read_text().splitlines()[1] == "sub-5\t45\tF"
    assert result.report and result.report.exists()

    # re-running the same exam replaces its session instead of duplicating it
    run(77, out, source)
    assert sorted(p.name for p in anat.glob("*.nii.gz")) == ["sub-5_ses-77_FLAIR.nii.gz", "sub-5_ses-77_T1w.nii.gz"]
    assert (out / "participants.tsv").read_text().count("sub-5") == 1


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "bids"
    result = run(77, out, FakeSource(tmp_path / "in"), dry_run=True)
    assert not out.exists()
    assert {e.series: e.status for e in result.entries}["Survey"] == "excluded"
