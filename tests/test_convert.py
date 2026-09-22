import json

from agora2bids.convert import parse_outputs


def touch(directory, stem, meta=None):
    (directory / f"{stem}.nii.gz").write_bytes(b"x")
    (directory / f"{stem}.json").write_text(json.dumps(meta or {}))
    return directory / f"{stem}.nii.gz"


def test_echo_and_phase_tokens_and_magnitude_part(tmp_path):
    files = [touch(tmp_path, "out_e1"), touch(tmp_path, "out_e1_ph"), touch(tmp_path, "out_e2"), touch(tmp_path, "out_e2_ph")]
    outputs, skipped = parse_outputs(files)
    got = {o.nifti.name: o.entities for o in outputs}
    assert got["out_e1.nii.gz"] == {"echo": "1", "part": "mag"}
    assert got["out_e2_ph.nii.gz"] == {"echo": "2", "part": "phase"}
    assert skipped == []


def test_single_output_has_no_part(tmp_path):
    outputs, _ = parse_outputs([touch(tmp_path, "out")])
    assert outputs[0].entities == {}


def test_derived_images_are_skipped(tmp_path):
    outputs, skipped = parse_outputs([touch(tmp_path, "out"), touch(tmp_path, "out_ADC"),
                                      touch(tmp_path, "out_x", {"ImageType": ["DERIVED", "SECONDARY"]})])
    assert [o.nifti.name for o in outputs] == ["out.nii.gz"]
    assert len(skipped) == 2


def test_precomputed_b0_fieldmap_detected_from_sidecar_image_type(tmp_path):
    # matches a real Philips "B0_multipTEecho_Unwrap" dataset: dcm2niix's own filename tokens ("e1" / "e1a") are
    # not meaningful here -- only the sidecar ImageType says which output is the field map
    fieldmap = touch(tmp_path, "out_e1", {"ImageType": ["ORIGINAL", "PRIMARY", "B0", "T1FFE"]})
    magnitude = touch(tmp_path, "out_e1a", {"ImageType": ["ORIGINAL", "PRIMARY", "M", "T1FFE", "MAGNITUDE"], "EchoTime": 0.002})
    outputs, skipped = parse_outputs([fieldmap, magnitude], fmap_kind="b0")
    assert skipped == []
    by_name = {o.nifti.name: o for o in outputs}
    assert by_name["out_e1.nii.gz"].suffix == "fieldmap"
    assert by_name["out_e1a.nii.gz"].suffix is None and by_name["out_e1a.nii.gz"].entities == {}


def test_two_echo_phasediff_still_gets_numbered_magnitude(tmp_path):
    # the classic dual-echo case: dcm2niix's e1/e2/e1_ph/e2_ph tokens ARE meaningful and must still drive naming
    files = [touch(tmp_path, "out_e1"), touch(tmp_path, "out_e1_ph"), touch(tmp_path, "out_e2"), touch(tmp_path, "out_e2_ph")]
    outputs, _ = parse_outputs(files, fmap_kind="b0")
    got = {o.nifti.name: o.entities for o in outputs}
    assert got["out_e1.nii.gz"] == {"echo": "1", "part": "mag"}
    assert got["out_e2_ph.nii.gz"] == {"echo": "2", "part": "phase"}
