from agora2bids.classify import SeriesMeta, classify, refine, family_of, family_from_technique
from agora2bids.dicom_info import DicomInfo
from agora2bids.params import ParamPool

META = SeriesMeta(name="scan")


def goal(**codes):
    base = {
        "EX_ACQ_scan_type": "MGUACQ_SCT_IMAGING",
        "EX_ACQ_imaging_sequence": "MGUACQ_SEQ_FFE",
        "EX_ACQ_fast_imaging_mode": "MGUACQ_FAST_TFE",
        "EX_DIFF_enable": "MGU_DIFF_TECH_NO",
        "EX_FLL_mode": "MGUFLL_SEL_NO",
    }
    base.update(codes)
    return ParamPool([{"Name": k, "Value": v} for k, v in base.items()])


def full(pool, dicom=None, meta=META):
    decision = classify(meta, pool, dicom)
    return decision


def test_exclusions():
    assert not classify(META, goal(EX_ACQ_scan_type="MGUACQ_SCT_SPECTRO"), None).include
    assert not classify(META, goal(EX_ACQ_smartscout_type="MPUACQ_SMARTPLAN_TYPE_BRAIN"), None).include
    assert not classify(META, ParamPool([{"Name": "RC_is_coca_scan", "Value": 1}]), None).include
    assert not classify(SeriesMeta(name="SmartBrain Survey"), goal(), None).include
    assert not classify(SeriesMeta(name="T1", is_refscan=True), goal(), None).include
    assert not classify(SeriesMeta(name="x", is_coil_survey=True), goal(), None).include
    assert not classify(META, None, DicomInfo(sop_class="1.2.840.10008.5.1.4.1.1.4.2", n_files=1)).include


def test_dwi_perf_fmap():
    assert classify(META, goal(EX_DIFF_enable="MGU_DIFF_TECH_DTI"), None).datatype == "dwi"
    assert classify(META, goal(EX_FLL_mode="MGUFLL_SEL_pCASL"), None).datatype == "perf"
    b0 = classify(META, goal(EX_ACQ_B0_map="MGUACQ_B0_MAP_YES"), None)
    assert (b0.datatype, b0.fmap_kind) == ("fmap", "b0")
    b1 = classify(META, goal(EX_ACQ_B1_map="MGUACQ_B1_MAP_YES"), None)
    assert (b1.datatype, b1.suffix) == ("fmap", "TB1map")


def test_dwi_precedes_func():
    pool = goal(EX_DIFF_enable="MGU_DIFF_TECH_DWI", EX_ACQ_fast_imaging_mode="MGUACQ_FAST_EPI",
                EX_DYN_study="MPUDYN_MODE_INDIVIDUAL")
    assert classify(META, pool, None).datatype == "dwi"


def epi_goal(dyn="MPUDYN_MODE_INDIVIDUAL", n=100):
    pool = goal(EX_ACQ_fast_imaging_mode="MGUACQ_FAST_EPI", EX_DYN_study=dyn)
    pool._by_name["EX_DYN_nr_scans"] = {"Name": "EX_DYN_nr_scans", "Value": n}
    return pool


def test_func_needs_dynamic_series():
    assert classify(META, epi_goal(), None).datatype == "func"
    assert not classify(META, epi_goal(n=3), None).include
    assert not classify(META, epi_goal(dyn="MPUDYN_MODE_NO"), None).include


def test_func_from_dicom_counts():
    pool = epi_goal(n=100)
    assert classify(META, pool, DicomInfo(epi=True, n_temporal=50, n_files=1)).datatype == "func"
    assert not classify(META, pool, DicomInfo(epi=True, n_temporal=2, n_files=1)).include


def test_phase_contrast_flow_is_excluded():
    assert not classify(META, goal(EX_PC_angio_mode="MPU_PC_ANGIO_PC"), None).include
    assert not classify(META, None, DicomInfo(phase_contrast=True, n_files=1, technique="T1TFE")).include


def test_angio():
    d = classify(META, goal(EX_PC_angio_mode="MPU_PC_ANGIO_INFLOW"), None)
    assert (d.datatype, d.suffix, d.entities) == ("anat", "angio", {"acq": "tof"})


def anat(seq, fast, **dicom):
    pool = goal(EX_ACQ_imaging_sequence=seq, EX_ACQ_fast_imaging_mode=fast)
    before = classify(META, pool, None)
    assert before.needs_timing and before.suffix is None
    info = DicomInfo(n_files=1, **dicom)
    return classify(META, pool, info)


def test_anat_spin_echo_contrasts():
    assert anat("MGUACQ_SEQ_SE", "MGUACQ_FAST_TSE", te=100.0, tr=4000.0).suffix == "T2w"
    assert anat("MGUACQ_SEQ_SE", "MGUACQ_FAST_TSE", te=25.0, tr=3000.0).suffix == "PDw"
    assert anat("MGUACQ_SEQ_SE", "MGUACQ_FAST_NO", te=12.0, tr=500.0).suffix == "T1w"


def test_anat_inversion_recovery():
    assert anat("MGUACQ_SEQ_IR", "MGUACQ_FAST_TSE", ti=2400.0).suffix == "FLAIR"
    stir = anat("MGUACQ_SEQ_IR", "MGUACQ_FAST_TSE", ti=200.0)
    assert (stir.suffix, stir.entities) == ("T2w", {"acq": "stir"})
    assert anat("MGUACQ_SEQ_IR", "MGUACQ_FAST_TSE", ti=400.0).suffix == "T1w"


def test_anat_gradient_echo():
    assert anat("MGUACQ_SEQ_FFE", "MGUACQ_FAST_NO", te=16.0, tr=48.0).suffix == "T2starw"
    assert anat("MGUACQ_SEQ_FFE", "MGUACQ_FAST_NO", te=4.6, tr=50.0).suffix == "T1w"
    assert anat("MGUACQ_SEQ_FFE", "MGUACQ_FAST_TFE", te=3.6, tr=8.0).suffix == "T1w"


def test_balanced_and_t2prep_turbo_gradient_echo_is_t2w():
    d = classify(META, goal(), DicomInfo(n_files=1, technique="B-TFE", te=1.4, tr=2.8))
    assert (d.suffix, d.entities) == ("T2w", {"acq": "balanced"})
    pool = goal()
    pool._by_name["EX_T2PREP_te"] = {"Name": "EX_T2PREP_te", "Value": 58.4}
    # a default T2-prep echo time without the enable flag must not make the scan T2-weighted
    assert classify(META, pool, DicomInfo(n_files=1, te=1.4, tr=3.0)).suffix == "T1w"
    pool._by_name["EX_T2PREP_enable"] = {"Name": "EX_T2PREP_enable", "Value": "YES"}
    assert classify(META, pool, DicomInfo(n_files=1, te=1.4, tr=3.0)).suffix == "T2w"


def test_refine_falls_back_to_goal_inversion_delay():
    pool = goal(EX_ACQ_imaging_sequence="MGUACQ_SEQ_IR", EX_ACQ_fast_imaging_mode="MGUACQ_FAST_TSE")
    pool._by_name["EX_IR_delay"] = {"Name": "EX_IR_delay", "Value": 2400}
    decision = classify(META, pool, None)
    refine(decision, family_of(pool, None), pool, DicomInfo(n_files=1))
    assert decision.suffix == "FLAIR"


def test_dicom_only_paths():
    info = DicomInfo(n_files=1, technique="FEEPI", epi=True, n_temporal=50)
    assert classify(META, None, info).datatype == "func"
    info = DicomInfo(n_files=1, technique="DwiSE", b_values={0.0, 1000.0})
    assert classify(META, None, info).datatype == "dwi"
    info = DicomInfo(n_files=1, technique="T1TFE", te=3.6, tr=8.0)
    assert classify(META, None, info).suffix == "T1w"
    info = DicomInfo(n_files=1, asl=True, technique="T1TFE")
    assert classify(META, None, info).datatype == "perf"


def test_dicom_only_b0_map_heuristic_warns():
    info = DicomInfo(n_files=2, technique="FFE", echo_times={2.0, 4.6}, image_type={"P", "M"})
    d = classify(META, None, info)
    assert (d.datatype, d.fmap_kind) == ("fmap", "b0") and d.warnings


def test_no_information_is_provisional():
    d = classify(META, None, None)
    assert d.include and d.provisional


def test_dicom_evidence_wins_and_is_reported_on_conflict():
    d = classify(META, goal(EX_DIFF_enable="MGU_DIFF_TECH_NO"), DicomInfo(n_files=1, b_values={1000.0}))
    assert d.datatype == "dwi" and any("diffusion" in w for w in d.warnings)
    # missing DICOM tags are not evidence against a GOAL flag
    assert classify(META, goal(EX_DIFF_enable="MGU_DIFF_TECH_DTI"), DicomInfo(n_files=1)).datatype == "dwi"


def test_technique_families():
    assert family_from_technique("FEEPI").epi
    assert family_from_technique("SEEPI").seq == "SE" and family_from_technique("SEEPI").epi
    assert (family_from_technique("TIR").seq, family_from_technique("TIR").fast) == ("IR", "TSE")
    assert (family_from_technique("T1TFE").seq, family_from_technique("T1TFE").fast) == ("FFE", "TFE")
    assert family_from_technique("B-FFE").balanced
