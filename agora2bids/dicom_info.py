"""Classification-relevant facts read from DICOM headers (classic and enhanced MR)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pydicom
from pydicom.tag import Tag

MR_SPECTROSCOPY_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.4.2"

_T = {
    "sop_class": Tag(0x0008, 0x0016),
    "image_type": Tag(0x0008, 0x0008),
    "frame_type": Tag(0x0008, 0x9007),
    "series_description": Tag(0x0008, 0x103E),
    "protocol_name": Tag(0x0018, 0x1030),
    "pulse_sequence_name": Tag(0x0018, 0x9005),
    "scanning_technique": Tag(0x2001, 0x1020),
    "scanning_sequence": Tag(0x0018, 0x0020),
    "tr": Tag(0x0018, 0x0080),
    "te": Tag(0x0018, 0x0081),
    "effective_te": Tag(0x0018, 0x9082),
    "ti": Tag(0x0018, 0x0082),
    "flip": Tag(0x0018, 0x1314),
    "echo_train": Tag(0x0018, 0x0091),
    "epi": Tag(0x0018, 0x9018),
    "n_temporal": Tag(0x0020, 0x0105),
    "temporal_id": Tag(0x0020, 0x0100),
    "b_value": Tag(0x0018, 0x9087),
    "b_factor_philips": Tag(0x2001, 0x1003),
    "phase_contrast": Tag(0x0018, 0x9014),
    "asl_contrast": Tag(0x0018, 0x9250),
    "asl_sequence": Tag(0x0018, 0x9251),
}
_WANTED = {tag: key for key, tag in _T.items()}


@dataclass
class DicomInfo:
    sop_class: str | None = None
    image_type: set[str] = field(default_factory=set)
    series_description: str = ""
    protocol_name: str = ""
    technique: str | None = None
    scanning_sequence: set[str] = field(default_factory=set)
    tr: float | None = None
    te: float | None = None
    ti: float | None = None
    flip: float | None = None
    echo_times: set[float] = field(default_factory=set)
    echo_train: int | None = None
    epi: bool | None = None
    n_temporal: int = 1
    b_values: set[float] = field(default_factory=set)
    phase_contrast: bool | None = None
    asl: bool = False
    n_files: int = 0

    @property
    def diffusion(self) -> bool:
        return "DIFFUSION" in self.image_type or any(b > 0 for b in self.b_values)

    @property
    def has_phase_or_real(self) -> bool:
        return any(t in self.image_type for t in ("P", "PHASE", "R", "REAL", "I", "IMAGINARY"))


def _walk(ds):
    for element in ds:
        yield element
        if element.VR == "SQ":
            for item in element.value:
                yield from _walk(item)


def _floats(values) -> list[float]:
    out = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def find_dicom_files(directory: str | Path) -> list[Path]:
    return sorted(p for p in Path(directory).rglob("*") if p.is_file())


def read_dicom_info(directory: str | Path, max_files: int = 200) -> DicomInfo:
    """Read the classification-relevant tags from (a sample of) the DICOM files under ``directory``."""
    files = find_dicom_files(directory)
    if len(files) > max_files:
        step = len(files) / max_files
        files = [files[int(i * step)] for i in range(max_files)]

    collected: dict[str, list] = {key: [] for key in _T}
    n_files = 0
    for path in files:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:  # noqa: BLE001 - not a DICOM file
            continue
        n_files += 1
        for element in _walk(ds):
            key = _WANTED.get(element.tag)
            if key is None or element.VR == "SQ" and key != "asl_sequence":
                continue
            if element.VR == "SQ":
                collected[key].append(True)
                continue
            value = element.value
            if isinstance(value, (list, tuple)) or hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
                collected[key].extend(list(value))
            else:
                collected[key].append(value)

    info = DicomInfo(n_files=n_files)
    if not n_files:
        return info

    def first(key):
        return next((v for v in collected[key] if v not in (None, "")), None)

    info.sop_class = str(first("sop_class")) if first("sop_class") else None
    info.image_type = {str(v).upper() for v in collected["image_type"] + collected["frame_type"]}
    info.series_description = str(first("series_description") or "")
    info.protocol_name = str(first("protocol_name") or "")
    technique = first("pulse_sequence_name") or first("scanning_technique")
    info.technique = str(technique) if technique else None
    info.scanning_sequence = {str(v).upper() for v in collected["scanning_sequence"]}

    tes = _floats(collected["te"] + collected["effective_te"])
    info.echo_times = {round(t, 3) for t in tes if t > 0}
    info.te = min(info.echo_times) if info.echo_times else None
    trs = [t for t in _floats(collected["tr"]) if t > 0]
    info.tr = statistics.median(trs) if trs else None
    tis = [t for t in _floats(collected["ti"]) if t > 0]
    info.ti = max(tis) if tis else None
    flips = _floats(collected["flip"])
    info.flip = flips[0] if flips else None
    trains = _floats(collected["echo_train"])
    info.echo_train = int(max(trains)) if trains else None
    epi = [str(v).upper() for v in collected["epi"]]
    info.epi = ("YES" in epi) if epi else None
    temporal = _floats(collected["n_temporal"])
    ids = {int(v) for v in _floats(collected["temporal_id"])}
    info.n_temporal = int(max(temporal + [len(ids), 1]))
    info.b_values = {b for b in _floats(collected["b_value"] + collected["b_factor_philips"])}
    pc = [str(v).upper() for v in collected["phase_contrast"]]
    info.phase_contrast = ("YES" in pc) if pc else None
    info.asl = bool(collected["asl_contrast"] or collected["asl_sequence"])
    return info
