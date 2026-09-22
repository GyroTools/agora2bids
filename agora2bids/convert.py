"""PAR/REC -> DICOM (parrec2dcm) and DICOM -> NIfTI + BIDS sidecar (dcm2niix)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .bids_writer import Output

# derived images dcm2niix appends a token for; they are not raw BIDS data
DERIVED_TOKENS = {"ADC", "TRACE", "FA", "COLFA", "TENSOR", "MOCO", "EQ"}
_ECHO = re.compile(r"^e(\d+)$")


def find_dcm2niix() -> str:
    exe = os.environ.get("DCM2NIIX_EXE")
    if exe and Path(exe).exists():
        return exe
    found = shutil.which("dcm2niix")
    if found:
        return found
    for name in ("dcm2niix.exe", "dcm2niix"):
        candidate = Path(sys.executable).parent / name
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("dcm2niix not found: `pip install dcm2niix` or set DCM2NIIX_EXE")


def find_par_file(directory: Path) -> Path:
    for pattern in ("*.par", "*.PAR"):
        matches = sorted(Path(directory).rglob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"no .par file found in {directory}")


def parrec_to_dicom(par_file: Path, out_dir: Path, params_file: Path | None) -> list[Path]:
    """Classic (single-frame), not enhanced: parrec2dcm's grouping only splits by pixel format/orientation, not by
    image type, so two distinct contrasts sharing geometry (e.g. a magnitude reference and a precomputed B0 field
    map) end up as two StackIDs in one Enhanced MR file. dcm2niix's classic-DICOM reader reliably splits a series
    by EchoTime/ImageType; its enhanced-MR reader does not split by StackID the same way and silently flattens both
    stacks into one 4D volume instead (confirmed against a real B0_multipTEecho_Unwrap dataset -- the magnitude and
    the field map came out as a single mislabeled 4D NIfTI, whereas classic mode correctly gave two)."""
    from parrec2dcm import convert

    return convert(par_file, out_dir, output_format="classic", params=[params_file] if params_file else None)


def run_dcm2niix(dicom_dir: Path, out_dir: Path) -> list[Path]:
    """Convert to gzipped NIfTI with a BIDS sidecar; dcm2niix does not create the output directory itself."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [find_dcm2niix(), "-b", "y", "-z", "y", "-f", "out", "-o", str(out_dir), str(dicom_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"dcm2niix failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
    return sorted(out_dir.glob("*.nii.gz"))


def parse_outputs(niftis: list[Path], fmap_kind: str | None = None) -> tuple[list[Output], list[str]]:
    """Turn dcm2niix outputs into ``Output`` records; returns (outputs, skipped-derived reasons).

    ``fmap_kind == "b0"``: an output whose own DICOM ``ImageType`` contains ``B0`` is Philips' own precomputed,
    already-unwrapped field map (in Hz) -- not a phase image a phasediff could be derived from. It is tagged
    ``suffix="fieldmap"`` straight from the sidecar, bypassing dcm2niix's filename token for it: that token is
    meaningless here (confirmed against a real ``B0_multipTEecho_Unwrap`` dataset, where dcm2niix produced
    ``out_e1``/``out_e1a`` -- "e1" for the field map, but "e1a" for its paired magnitude image is not a real echo
    number and must not end up as an entity in the BIDS name).
    """
    outputs: list[Output] = []
    skipped: list[str] = []
    parsed: list[tuple[Path, dict[str, str], bool, bool]] = []  # nifti, entities, derived, is_b0_fieldmap
    for nifti in niftis:
        image_type = _sidecar_image_type(nifti)
        derived = "DERIVED" in image_type
        is_b0_fieldmap = fmap_kind == "b0" and "B0" in image_type
        entities: dict[str, str] = {}
        if not is_b0_fieldmap:
            stem = nifti.name[: -len(".nii.gz")]
            extra: list[str] = []
            for token in stem.split("_")[1:]:
                up = token.upper()
                if m := _ECHO.match(token):
                    entities["echo"] = m.group(1)
                elif up == "PH":
                    entities["part"] = "phase"
                elif up == "REAL":
                    entities["part"] = "real"
                elif up == "IMAGINARY":
                    entities["part"] = "imag"
                elif up in DERIVED_TOKENS:
                    derived = True
                else:
                    extra.append(token)
            if extra:
                entities["rec"] = "".join(extra)
        parsed.append((nifti, entities, derived, is_b0_fieldmap))

    has_fieldmap = any(is_b0 for _, _, _, is_b0 in parsed)
    has_phase_like = any(e.get("part") for _, e, _, is_b0 in parsed if not is_b0)
    for nifti, entities, derived, is_b0_fieldmap in parsed:
        if derived:
            skipped.append(f"{nifti.name}: derived image")
            continue
        if is_b0_fieldmap:
            outputs.append(Output(nifti=nifti, suffix="fieldmap"))
            continue
        if has_fieldmap:
            # the lone magnitude reference paired with a precomputed field map: no echo/rec noise from its token
            entities = {}
        elif has_phase_like and "part" not in entities:
            entities["part"] = "mag"
        outputs.append(Output(nifti=nifti, entities=entities))
    return outputs, skipped


def _sidecar_image_type(nifti: Path) -> set[str]:
    sidecar = nifti.with_name(nifti.name[: -len(".nii.gz")] + ".json")
    if not sidecar.exists():
        return set()
    try:
        image_type = json.loads(sidecar.read_text(encoding="utf-8")).get("ImageType") or []
    except ValueError:
        return set()
    return {str(t).upper() for t in image_type}
