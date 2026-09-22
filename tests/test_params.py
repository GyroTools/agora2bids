from pathlib import Path

from agora2bids.params import ParamPool

FIXTURE = Path(__file__).parent / "fixtures" / "goal_min.json"


def test_codes_decoded_from_enum_descriptions():
    pool = ParamPool.from_file(FIXTURE)
    assert pool.code("EX_ACQ_scan_type") == "MGUACQ_SCT_IMAGING"
    assert pool.code("EX_ACQ_fast_imaging_mode") == "MGUACQ_FAST_TFE"
    assert pool.code("EX_DIFF_enable") == "MGU_DIFF_TECH_NO"
    assert pool.code("EX_PROC_image_types")[0] == "MGU_ITYP_MODULUS"


def test_fallback_table_used_without_enum_descriptions():
    pool = ParamPool([{"Name": "EX_DIFF_enable", "Value": 2, "Properties": {"EnumDescription": 999}}])
    assert pool.code("EX_DIFF_enable") == "MGU_DIFF_TECH_DTI"


def test_string_values_are_passed_through_and_missing_is_none():
    pool = ParamPool([{"Name": "EX_FLL_mode", "Value": "MGUFLL_SEL_pCASL"}])
    assert pool.code("EX_FLL_mode") == "MGUFLL_SEL_pCASL"
    assert pool.code("EX_ACQ_B0_map") is None
    assert pool.number("EX_ACQ_B0_map") is None


def test_task_parameters_json_shape(tmp_path):
    import json

    path = tmp_path / "parameters.json"
    path.write_text(json.dumps([{"name": "GOAL", "parameters": [{"Name": "RC_is_coca_scan", "Value": 1}]}]))
    assert ParamPool.from_file(path).number("RC_is_coca_scan") == 1


def test_from_parametersets_objects():
    from types import SimpleNamespace

    ps = SimpleNamespace(parameters=[{"Name": "EX_ACQ_scan_type", "Value": 1, "Properties": {"EnumDescription": 1}}],
                         properties={"EnumDescriptions": [{"ID": 1, "Values": [{"Value": "X_IMG", "ValueInt": 0}, {"Value": "X_SPEC", "ValueInt": 1}]}]})
    assert ParamPool.from_parametersets([ps]).code("EX_ACQ_scan_type") == "X_SPEC"
