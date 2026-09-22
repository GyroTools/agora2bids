import json

from agora2bids import bids_writer as bw


def make_nifti(directory, stem, meta=None, extra=()):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.nii.gz").write_bytes(b"nii")
    (directory / f"{stem}.json").write_text(json.dumps(meta or {"RepetitionTime": 2.0}))
    for ext in extra:
        (directory / f"{stem}{ext}").write_text("0 1000")
    return directory / f"{stem}.nii.gz"


def test_basename_uses_bids_entity_order_and_cleans_labels():
    name = bw.basename({"run": "2", "task": "rest-state", "sub": "1", "ses": "9", "acq": "b0 map"}, "bold")
    assert name == "sub-1_ses-9_task-reststate_acq-b0map_run-2_bold"


def test_build_session_names_runs_and_sidecars(tmp_path):
    src = tmp_path / "src"
    t1a = bw.Item("anat", "T1w", {}, [bw.Output(make_nifti(src, "a"))], order=0)
    t1b = bw.Item("anat", "T1w", {}, [bw.Output(make_nifti(src, "b"))], order=1)
    bold = bw.Item("func", "bold", {"task": "unknown"}, [bw.Output(make_nifti(src, "c"))], order=2)
    dwi = bw.Item("dwi", "dwi", {}, [bw.Output(make_nifti(src, "d", extra=(".bval", ".bvec")))], order=3)
    fmap = bw.Item("fmap", "", {"acq": "b0map"}, [
        bw.Output(make_nifti(src, "e1", ), {"echo": "1", "part": "mag"}),
        bw.Output(make_nifti(src, "e1_ph"), {"echo": "1", "part": "phase"}),
    ], order=4, fmap_kind="b0")
    out = tmp_path / "ses"
    records = bw.build_session([t1a, t1b, bold, dwi, fmap], out, "7", "42", "rest")
    names = sorted(r["name"] for r in records)
    assert names == sorted([
        "sub-7_ses-42_acq-b0map_magnitude1", "sub-7_ses-42_acq-b0map_phase1",
        "sub-7_ses-42_run-1_T1w", "sub-7_ses-42_run-2_T1w",
        "sub-7_ses-42_task-rest_bold", "sub-7_ses-42_dwi",
    ])
    assert (out / "dwi" / "sub-7_ses-42_dwi.bval").exists()
    assert (out / "dwi" / "sub-7_ses-42_dwi.bvec").exists()
    bold_meta = json.loads((out / "func" / "sub-7_ses-42_task-rest_bold.json").read_text())
    assert bold_meta["TaskName"] == "rest"
    # IntendedFor belongs on the file that IS the field map (phase1 here), not the magnitude reference
    phase_meta = json.loads((out / "fmap" / "sub-7_ses-42_acq-b0map_phase1.json").read_text())
    assert "ses-42/func/sub-7_ses-42_task-rest_bold.nii.gz" in phase_meta["IntendedFor"]
    assert all(not p.startswith("ses-42/fmap") for p in phase_meta["IntendedFor"])
    magnitude_meta = json.loads((out / "fmap" / "sub-7_ses-42_acq-b0map_magnitude1.json").read_text())
    assert "IntendedFor" not in magnitude_meta


def test_precomputed_fieldmap_gets_units_hz_and_plain_magnitude_name(tmp_path):
    # a Philips-precomputed, already-unwrapped B0 map (see convert.parse_outputs): one "fieldmap" output (suffix
    # set directly, no echo/part entities) paired with one plain magnitude reference (no entities at all)
    src = tmp_path / "src"
    fmap = bw.Item("fmap", "", {"acq": "b0map"}, [
        bw.Output(make_nifti(src, "out_e1"), suffix="fieldmap"),
        bw.Output(make_nifti(src, "out_e1a")),
    ], order=0, fmap_kind="b0")
    out = tmp_path / "ses"
    records = bw.build_session([fmap], out, "7", "42", "rest")
    names = sorted(r["name"] for r in records)
    assert names == ["sub-7_ses-42_acq-b0map_fieldmap", "sub-7_ses-42_acq-b0map_magnitude"]
    fieldmap_meta = json.loads((out / "fmap" / "sub-7_ses-42_acq-b0map_fieldmap.json").read_text())
    assert fieldmap_meta["Units"] == "Hz"
    magnitude_meta = json.loads((out / "fmap" / "sub-7_ses-42_acq-b0map_magnitude.json").read_text())
    assert "Units" not in magnitude_meta and "IntendedFor" not in magnitude_meta


def test_dataset_description_created_once(tmp_path):
    bw.ensure_dataset_description(tmp_path, "First")
    bw.ensure_dataset_description(tmp_path, "Second")
    meta = json.loads((tmp_path / "dataset_description.json").read_text())
    assert meta["Name"] == "First" and meta["BIDSVersion"] and meta["DatasetType"] == "raw"


def test_participants_upsert_and_age(tmp_path):
    bw.upsert_participant(tmp_path, "2", "f", "34")
    bw.upsert_participant(tmp_path, "1", "m", "n/a")
    bw.upsert_participant(tmp_path, "2", "f", "35")
    lines = (tmp_path / "participants.tsv").read_text().splitlines()
    assert lines == ["participant_id\tage\tsex", "sub-1\tn/a\tM", "sub-2\t35\tF"]
    assert (tmp_path / "participants.json").exists()
    assert bw.age_at("1980-12-05", "2025-12-04T10:00:00Z") == "44"
    assert bw.age_at("1980-12-05", "2025-12-05") == "45"
    assert bw.age_at(None, "2025-12-05") == "n/a"


def test_replace_session_swaps_previous_conversion(tmp_path):
    root = tmp_path / "bids"
    old = root / "sub-1" / "ses-2" / "anat"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("x")
    staged = tmp_path / "stage" / "sub-1" / "ses-2" / "anat"
    staged.mkdir(parents=True)
    (staged / "new.txt").write_text("y")
    bw.replace_session(tmp_path / "stage", root, "1", "2")
    assert (root / "sub-1" / "ses-2" / "anat" / "new.txt").exists()
    assert not (root / "sub-1" / "ses-2" / "anat" / "old.txt").exists()
