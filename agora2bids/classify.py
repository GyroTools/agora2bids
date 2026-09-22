"""Decide whether a series belongs in the BIDS dataset and, if so, which BIDS datatype/suffix it gets.

Rules (first match wins), fitted on the Philips ExamCard corpus and checked against real Philips DICOM:

1. exclude   spectroscopy, SmartPlan/coil-reference/survey scans
2. dwi       EX_DIFF_enable DWI/DTI, or non-zero b-values / ImageType DIFFUSION in the DICOM
3. perf      EX_FLL_mode != NO, or ASL tags in the DICOM
4. fmap      EX_ACQ_B0_map == YES (B0), EX_ACQ_B1_map == YES (B1); DICOM heuristic without GOAL
5. flow      phase-contrast flow has no raw BIDS datatype -> excluded
6. func      EPI with dynamics: fast mode EPI/TFE_EPI, dynamic study and more than 5 dynamics
7. angio     inflow / contrast-enhanced angio -> anat/angio
8. anat      suffix from the technique family and the actual TR/TE/TI (see ``anat_suffix``)

GOAL parameters carry the operator's intent; DICOM carries what was measured. Intent flags (B0/B1 map, ASL mode,
dynamic study, exclusions) prefer GOAL, measured values (b-values, dynamic count, TR/TE/TI) prefer DICOM, and a
conflict is reported as a warning with the physical (DICOM) evidence winning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dicom_info import MR_SPECTROSCOPY_SOP_CLASS, DicomInfo
from .params import ParamPool

SURVEY_NAME = re.compile(r"survey|scout|locali[sz]er", re.I)
GOAL_MARKERS = ("EX_ACQ_imaging_sequence", "EX_ACQ_scan_type", "EX_DIFF_enable", "EX_ACQ_fast_imaging_mode")

# thresholds fitted on the ExamCard corpus (milliseconds)
FLAIR_MIN_TI = 1500
STIR_MAX_TI = 250
SE_T2_MIN_TE = 45
SE_PD_MIN_TR = 1000
GRE_T2STAR_MIN_TE = 10
FUNC_MIN_DYNAMICS = 5  # more than this many


@dataclass
class SeriesMeta:
    name: str = ""
    is_refscan: bool = False
    is_coil_survey: bool = False


@dataclass
class Decision:
    include: bool
    datatype: str | None = None
    suffix: str | None = None
    entities: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    provisional: bool = False  # decided without header data; re-run once the DICOM is available
    needs_timing: bool = False  # anat suffix still to be resolved from TR/TE/TI
    warnings: list[str] = field(default_factory=list)
    fmap_kind: str | None = None  # "b0" or "b1"


@dataclass
class Family:
    seq: str | None = None  # SE, IR, FFE, MIXED, ...
    fast: str | None = None  # NO, TSE, TFE, EPI, TFE_EPI, GRASE
    balanced: bool = False
    t2_hint: str | None = None  # "T2w" or "T2starw" from technique names like T2TFE / T2FFE
    t2prep: bool = False

    @property
    def epi(self) -> bool:
        return self.fast in ("EPI", "TFE_EPI")


def family_from_technique(technique: str | None, scanning_sequence: set[str] | None = None) -> Family:
    """Map a Philips technique string (``T1TFE``, ``FEEPI``, ``DwiSE``, ``B-TFE`` ...) to a family."""
    fam = Family()
    t = (technique or "").upper()
    if t:
        fam.balanced = t.startswith("B-")
        if t.startswith("T2") and "TFE" in t:
            fam.t2_hint = "T2w"
        elif t.startswith("T2") and "FFE" in t:
            fam.t2_hint = "T2starw"
        if "EPI" in t:
            fam.fast = "TFE_EPI" if t.startswith("TFE") else "EPI"
        elif "TFE" in t:
            fam.fast = "TFE"
        elif t in ("TSE", "TIR") or t.endswith("TSE"):
            fam.fast = "TSE"
        elif "GRASE" in t:
            fam.fast = "GRASE"
        else:
            fam.fast = "NO"
        if "FFE" in t or "TFE" in t:
            fam.seq = "FFE"
        elif "IR" in t:
            fam.seq = "IR"
        elif "SE" in t:
            fam.seq = "SE"
        elif "MIX" in t or "MX" in t:
            fam.seq = "MIXED"
        if fam.seq:
            return fam
    seqs = scanning_sequence or set()
    if "IR" in seqs:
        fam.seq = "IR"
    elif "SE" in seqs:
        fam.seq = "SE"
    elif "GR" in seqs:
        fam.seq = "FFE"
    if "EP" in seqs:
        fam.fast = "EPI"
    fam.fast = fam.fast or "NO"
    return fam


def _strip(code, prefix: str) -> str | None:
    return code[len(prefix):] if isinstance(code, str) and code.startswith(prefix) else code


def _has_goal(pool: ParamPool | None) -> bool:
    return pool is not None and any(pool.has(n) for n in GOAL_MARKERS)


def family_of(pool: ParamPool | None, dicom: DicomInfo | None) -> Family:
    if _has_goal(pool):
        fam = Family(
            seq=_strip(pool.code("EX_ACQ_imaging_sequence"), "MGUACQ_SEQ_"),
            fast=_strip(pool.code("EX_ACQ_fast_imaging_mode"), "MGUACQ_FAST_"),
        )
        # EX_T2PREP_te always carries a default value; only the enable flag says the prepulse is used
        fam.t2prep = _is_yes(pool.code("EX_T2PREP_enable"))
        if dicom is not None and dicom.technique:
            hinted = family_from_technique(dicom.technique)
            fam.balanced, fam.t2_hint = hinted.balanced, hinted.t2_hint
        return fam
    if dicom is not None:
        return family_from_technique(dicom.technique, dicom.scanning_sequence)
    return Family()


def _resolve(name: str, goal: bool | None, measured: bool | None, warnings: list[str], prefer: str) -> bool:
    """Combine a GOAL flag and a DICOM observation; ``prefer`` says which wins when both exist and differ."""
    if goal is None:
        return bool(measured)
    if measured is None:
        return bool(goal)
    if goal != measured:
        warnings.append(f"{name}: GOAL says {goal}, DICOM says {measured}; using {prefer}")
        return goal if prefer == "goal" else measured
    return goal


def _is_yes(code) -> bool:
    return isinstance(code, str) and code.upper().endswith("YES")


def classify(meta: SeriesMeta, pool: ParamPool | None, dicom: DicomInfo | None) -> Decision:
    warnings: list[str] = []
    goal = _has_goal(pool)

    # 1. exclusions
    if meta.is_refscan or meta.is_coil_survey:
        return Decision(False, reason="reference/coil survey scan (Agora flag)")
    name_source = " ".join(filter(None, [meta.name, dicom.series_description if dicom else "", dicom.protocol_name if dicom else ""]))
    if SURVEY_NAME.search(name_source):
        return Decision(False, reason="survey/scout/localizer by name")
    if goal:
        if pool.code("EX_ACQ_scan_type") == "MGUACQ_SCT_SPECTRO":
            return Decision(False, reason="spectroscopy")
        smart = pool.code("EX_ACQ_smartscout_type")
        if isinstance(smart, str) and not smart.endswith("_NONE"):
            return Decision(False, reason="SmartPlan survey scan")
    if pool is not None and pool.number("RC_is_coca_scan") == 1:
        return Decision(False, reason="SENSE reference (COCA) scan")
    if dicom is not None:
        if dicom.sop_class == MR_SPECTROSCOPY_SOP_CLASS:
            return Decision(False, reason="spectroscopy (SOP class)")
        if "LOCALIZER" in dicom.image_type:
            return Decision(False, reason="localizer (ImageType)")

    fam = family_of(pool, dicom)

    # 2. dwi
    dwi_goal = (pool.code("EX_DIFF_enable") in ("MGU_DIFF_TECH_DWI", "MGU_DIFF_TECH_DTI")) if goal else None
    dwi_dicom = True if dicom is not None and dicom.diffusion else None  # missing tags are not evidence
    if _resolve("diffusion", dwi_goal, dwi_dicom, warnings, "dicom"):
        return Decision(True, "dwi", "dwi", reason="diffusion", warnings=warnings)

    # 3. perf
    fll = pool.code("EX_FLL_mode") if goal else None
    asl_goal = (fll != "MGUFLL_SEL_NO") if isinstance(fll, str) else None
    asl_dicom = dicom.asl if dicom is not None and dicom.asl else None
    if _resolve("ASL", asl_goal, asl_dicom, warnings, "goal"):
        return Decision(True, "perf", "asl", reason="arterial spin labeling", warnings=warnings)

    # 4. fmap
    if goal and pool.code("EX_ACQ_B0_map") == "MGUACQ_B0_MAP_YES":
        return Decision(True, "fmap", None, {"acq": "b0map"}, "B0 field map", fmap_kind="b0", warnings=warnings)
    if goal and pool.code("EX_ACQ_B1_map") == "MGUACQ_B1_MAP_YES":
        return Decision(True, "fmap", "TB1map", {"acq": "b1map"}, "B1 field map", fmap_kind="b1", warnings=warnings)
    if not goal and dicom is not None and _looks_like_b0_map(dicom, fam):
        warnings.append("B0 field map detected heuristically (no GOAL parameters)")
        return Decision(True, "fmap", None, {"acq": "b0map"}, "B0 field map (heuristic)", fmap_kind="b0", warnings=warnings)

    # 5. phase-contrast flow: no raw BIDS datatype
    pc_goal = (pool.code("EX_PC_angio_mode") in ("MPU_PC_ANGIO_PC", "MPU_PC_ANGIO_MULTI")) if goal else None
    pc_dicom = None
    if dicom is not None and (
        dicom.phase_contrast or "FLOW_ENCODED" in dicom.image_type or any(t.endswith("_PCA") for t in dicom.image_type)
    ):
        pc_dicom = True
    if _resolve("phase-contrast", pc_goal, pc_dicom, warnings, "goal"):
        return Decision(False, reason="phase-contrast flow (no BIDS raw datatype)", warnings=warnings)

    # 6. func
    epi_goal = fam.epi if goal else None
    epi_dicom = None
    if dicom is not None and (dicom.epi or family_from_technique(dicom.technique).epi):
        epi_dicom = True
    epi = _resolve("EPI", epi_goal, epi_dicom, warnings, "dicom")
    if epi:
        if dicom is not None:
            dynamic = dicom.n_temporal > FUNC_MIN_DYNAMICS
        elif goal:
            dyn = pool.code("EX_DYN_study")
            n = pool.number("EX_DYN_nr_scans") or 0
            dynamic = isinstance(dyn, str) and dyn != "MPUDYN_MODE_NO" and n > FUNC_MIN_DYNAMICS
        else:
            return Decision(True, provisional=True, reason="EPI without header data", warnings=warnings)
        if dynamic:
            return Decision(True, "func", "bold", {"task": "unknown"}, "EPI with dynamics", warnings=warnings)
        return Decision(False, reason="EPI without a dynamic series (test/shim/calibration)", warnings=warnings)

    # 7. angio
    angio = pool.code("EX_PC_angio_mode") if goal else None
    if angio == "MPU_PC_ANGIO_INFLOW":
        return Decision(True, "anat", "angio", {"acq": "tof"}, "inflow angiography", warnings=warnings)
    if angio == "MPU_PC_ANGIO_CE":
        return Decision(True, "anat", "angio", {"acq": "ce"}, "contrast-enhanced angiography", warnings=warnings)

    # 8. anat
    return _classify_anat(fam, pool, dicom, warnings, goal)


def _looks_like_b0_map(dicom: DicomInfo, fam: Family) -> bool:
    return len(dicom.echo_times) >= 2 and dicom.has_phase_or_real and fam.seq in ("FFE", None)


def _classify_anat(fam: Family, pool: ParamPool | None, dicom: DicomInfo | None, warnings: list[str], goal: bool) -> Decision:
    if fam.seq is None:
        if dicom is None and not goal:
            return Decision(True, provisional=True, reason="needs header data", warnings=warnings)
        return Decision(False, reason="technique unknown", warnings=warnings)
    if fam.seq not in ("SE", "IR", "FFE"):
        return Decision(False, reason=f"no BIDS mapping for technique {fam.seq}", warnings=warnings)
    decision = Decision(True, "anat", None, reason=f"anatomical ({fam.seq}/{fam.fast})", warnings=warnings)
    if dicom is None:
        decision.needs_timing = True
        decision.provisional = not goal
        return decision
    return refine(decision, fam, pool, dicom)


def anat_suffix(fam: Family, tr: float | None, te: float | None, ti: float | None) -> tuple[str, dict[str, str], str | None]:
    """Contrast suffix (and extra entities) from technique family and timing in ms; third item is a warning."""
    if fam.seq == "IR":
        if ti is None:
            return "T1w", {}, "inversion time unknown; assumed T1w"
        if ti >= FLAIR_MIN_TI:
            return "FLAIR", {}, None
        if ti <= STIR_MAX_TI:
            return "T2w", {"acq": "stir"}, None
        return "T1w", {}, None
    if fam.seq == "SE":
        if te is None:
            return "T1w", {}, "echo time unknown; assumed T1w"
        if te >= SE_T2_MIN_TE:
            return "T2w", {}, None
        if tr is not None and tr >= SE_PD_MIN_TR:
            return "PDw", {}, None
        return "T1w", {}, (None if tr is not None else "repetition time unknown; assumed T1w")
    # gradient echo
    if fam.balanced or fam.t2prep or fam.t2_hint == "T2w":
        return "T2w", ({"acq": "balanced"} if fam.balanced else {}), None
    if fam.t2_hint == "T2starw":
        return "T2starw", {}, None
    if fam.fast in (None, "NO") and te is not None and te >= GRE_T2STAR_MIN_TE:
        return "T2starw", {}, None
    return "T1w", {}, (None if te is not None else "echo time unknown; assumed T1w")


def refine(decision: Decision, fam: Family, pool: ParamPool | None, dicom: DicomInfo) -> Decision:
    """Resolve the anat suffix from the measured TR/TE/TI (GOAL inversion delay as fallback)."""
    ti = dicom.ti
    if ti is None and pool is not None:
        ti = pool.number("EX_IR_delay")
    suffix, entities, warning = anat_suffix(fam, dicom.tr, dicom.te, ti)
    decision.suffix = suffix
    decision.entities = {**decision.entities, **entities}
    decision.needs_timing = False
    if warning:
        decision.warnings.append(warning)
    return decision
