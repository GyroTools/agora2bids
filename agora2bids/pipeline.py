"""Convert one Agora exam into a BIDS dataset directory."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bids_writer
from .agora_source import human_size
from .classify import SeriesMeta, classify
from .convert import (find_par_file, parrec_to_dicom, parse_outputs,
                      run_dcm2niix)
from .dicom_info import read_dicom_info
from .report import Entry, write_report

log = logging.getLogger("agora2bids")


@dataclass
class Result:
    entries: list[Entry] = field(default_factory=list)
    session_dir: Path | None = None
    report: Path | None = None

    @property
    def failed(self) -> int:
        return sum(1 for e in self.entries if e.status == "failed")


def run(
    exam_id: int,
    output: Path,
    source: Any,
    *,
    dry_run: bool = False,
    keep_temp: bool = False,
    task_label: str = "unknown",
    dataset_name: str = "Agora export",
    temp_root: Path | None = None,
) -> Result:
    output = Path(output)
    exam = source.get_exam(exam_id)
    patient = source.patient(exam)
    sub, ses = str(patient.id), str(exam.id)
    log.info("exam %s -> sub-%s ses-%s", exam_id, sub, ses)

    tmp = Path(tempfile.mkdtemp(prefix="agora2bids_", dir=temp_root))
    result = Result()
    items: list[bids_writer.Item] = []
    item_entry: dict[int, Entry] = {}
    try:
        log.info("Listing imaging datasets ...")
        candidates = source.candidates(exam)
        log.info("Found %d imaging dataset(s)", len(candidates))
        for n, cand in enumerate(candidates, start=1):
            log.info("[%d/%d] %s (dataset %s, %s, %s)", n, len(candidates), cand.series.name, cand.dataset.id,
                     cand.kind, human_size(getattr(cand.dataset, "total_size", None)))
            entry, item = _process(cand, source, tmp, dry_run, keep_temp)
            log.info("  -> %s%s", entry.status, f": {entry.datatype}" if entry.datatype else f" ({entry.reason})")
            result.entries.append(entry)
            if item is not None:
                items.append(item)
                item_entry[id(item)] = entry
        if not dry_run:
            result.session_dir = _write_bids(items, item_entry, output, tmp, sub, ses, patient, exam, task_label, dataset_name)
            result.report = write_report(output, exam_id, sub, ses, result.entries)
    finally:
        if keep_temp:
            log.info("temporary files kept in %s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return result


def _process(cand, source, tmp: Path, dry_run: bool, keep_temp: bool) -> tuple[Entry, bids_writer.Item | None]:
    series, dataset = cand.series, cand.dataset
    entry = Entry(series=series.name, dataset_id=dataset.id, kind=cand.kind, status="excluded")
    try:
        meta = SeriesMeta(
            name=series.name,
            is_refscan=bool(getattr(series, "is_refscan", False)),
            is_coil_survey=bool(getattr(series, "is_coil_survey", False)),
        )
        pool = source.parameters(dataset)
        pre = classify(meta, pool, None)
        entry.warnings.extend(pre.warnings)
        if not pre.include:
            entry.reason = pre.reason
            return entry, None
        if dry_run:
            entry.status = "would-convert"
            entry.datatype = pre.datatype
            entry.reason = f"provisional ({pre.reason}); headers needed" if pre.provisional or pre.needs_timing else pre.reason
            return entry, None

        work = tmp / f"ds_{dataset.id}"
        log.info("  downloading ...")
        raw = source.download(dataset, work / "raw")
        if cand.kind == "parrec":
            params_file = source.write_parameter_file(pool, work / "params.json") if pool else None
            dicom_dir = work / "dicom"
            parrec_to_dicom(find_par_file(raw), dicom_dir, params_file)
        else:
            dicom_dir = raw

        dicom = read_dicom_info(dicom_dir)
        final = classify(meta, pool, dicom)
        entry.warnings = [w for w in final.warnings]
        if not final.include:
            entry.reason = final.reason
            _cleanup(work, keep_temp, keep_nifti=False)
            return entry, None

        niftis = run_dcm2niix(dicom_dir, work / "nifti")
        outputs, skipped = parse_outputs(niftis, fmap_kind=final.fmap_kind)
        entry.warnings.extend(skipped)
        _cleanup(work, keep_temp, keep_nifti=True)
        if not outputs:
            entry.status, entry.reason = "failed", "dcm2niix produced no usable NIfTI"
            return entry, None
        entry.status, entry.datatype, entry.reason = "converted", final.datatype, final.reason
        item = bids_writer.Item(
            datatype=final.datatype, suffix=final.suffix or "", entities=dict(final.entities), outputs=outputs,
            order=cand.order, source=series.name, fmap_kind=final.fmap_kind,
        )
        return entry, item
    except Exception as exc:  # noqa: BLE001 - one bad dataset must not stop the exam
        log.exception("dataset %s failed", getattr(dataset, "id", "?"))
        entry.status, entry.reason = "failed", f"{type(exc).__name__}: {exc}"
        return entry, None


def _cleanup(work: Path, keep_temp: bool, keep_nifti: bool) -> None:
    if keep_temp:
        return
    for sub in ("raw", "dicom") + (() if keep_nifti else ("nifti",)):
        shutil.rmtree(work / sub, ignore_errors=True)


def _write_bids(items, item_entry, output: Path, tmp: Path, sub, ses, patient, exam, task_label, dataset_name) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    bids_writer.ensure_dataset_description(output, dataset_name)
    staged = tmp / "stage"
    session_stage = staged / f"sub-{sub}" / f"ses-{ses}"
    if items:
        records = bids_writer.build_session(items, session_stage, sub, ses, task_label)
        for record in records:
            entry = item_entry[id(record["item"])]
            entry.files.append(f"sub-{sub}/ses-{ses}/{record['datatype']}/{record['name']}.nii.gz")
    session_dir = bids_writer.replace_session(staged, output, sub, ses)
    if items:
        bids_writer.upsert_participant(
            output, sub, getattr(patient, "sex", None),
            bids_writer.age_at(getattr(patient, "birth_date", None), getattr(exam, "start_time", None)),
        )
    return session_dir
