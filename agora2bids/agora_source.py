"""Everything that talks to Agora (via gtagora-connector-py)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .params import ParamPool

log = logging.getLogger("agora2bids")

DICOM_TYPE = 300  # gtagora DatasetType.DICOM
PARREC_TYPE = 101  # gtagora DatasetType.PHILIPS_PARREC


@dataclass
class Candidate:
    series: Any
    dataset: Any
    kind: str  # "dicom" or "parrec"
    order: int


@dataclass
class PatientInfo:
    id: int
    sex: str | None
    birth_date: str | None


def human_size(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def patient_info(exam, agora) -> PatientInfo:
    """The exam's patient.

    Agora serializes ``exam.patient`` as the full nested patient object (v1 and v2 API), or ``None``; an older
    payload may still carry a bare id, which is resolved with one extra request.
    """
    value = getattr(exam, "patient", None)
    if value is None:
        raise ValueError(f"exam {getattr(exam, 'id', '?')} has no patient assigned; cannot choose a BIDS subject")
    if isinstance(value, dict):
        from gtagora.models.patient import Patient

        value = Patient.from_response(value, http_client=getattr(agora, "http_client", None))
    elif isinstance(value, int):
        value = agora.get_patient(value)
    return PatientInfo(id=value.id, sex=getattr(value, "sex", None), birth_date=getattr(value, "birth_date", None))


class AgoraSource:
    def __init__(self, url: str, api_key: str):
        from gtagora.agora import Agora

        self.agora = Agora.create(url, api_key=api_key)

    def get_exam(self, exam_id: int):
        return self.agora.get_exam(exam_id)

    def patient(self, exam) -> PatientInfo:
        return patient_info(exam, self.agora)

    def candidates(self, exam) -> list[Candidate]:
        """Imaging datasets of the exam: DICOM, or PAR/REC when a series has no DICOM."""
        out: list[Candidate] = []
        series_list = sorted(exam.get_series(), key=lambda s: (getattr(s, "acquisition_number", None) or 0, s.id))
        for order, series in enumerate(series_list):
            types = list(getattr(series, "dataset_types", None) or [])
            datasets = series.get_datasets() if (not types or DICOM_TYPE in types or PARREC_TYPE in types) else []
            by_type = {t: [d for d in datasets if getattr(d, "type", None) == t] for t in (DICOM_TYPE, PARREC_TYPE)}
            if by_type[DICOM_TYPE]:
                kind, chosen = "dicom", by_type[DICOM_TYPE]
            elif by_type[PARREC_TYPE]:
                kind, chosen = "parrec", by_type[PARREC_TYPE]
            else:
                continue
            out.extend(Candidate(series, d, kind, order) for d in chosen)
        return out

    @staticmethod
    def parameters(dataset) -> ParamPool | None:
        try:
            pool = ParamPool.from_parametersets(dataset.get_parametersets())
        except Exception:  # noqa: BLE001 - datasets without parameter sets
            return None
        return pool if len(pool) else None

    @staticmethod
    def write_parameter_file(pool: ParamPool, path: Path) -> Path:
        path.write_text(json.dumps(pool.records(), default=str), encoding="utf-8")
        return path

    @staticmethod
    def download(dataset, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=True)
        dataset.download(target)
        return target
