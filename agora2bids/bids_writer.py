"""BIDS naming, session assembly and dataset-level files."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__

BIDS_VERSION = "1.10.0"
ENTITY_ORDER = ["sub", "ses", "task", "acq", "ce", "rec", "dir", "run", "echo", "part"]
SIDECAR_EXTENSIONS = (".json", ".bval", ".bvec")


def clean_label(value: str) -> str:
    """BIDS labels are alphanumeric only."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)) or "x"


def basename(entities: dict[str, str], suffix: str) -> str:
    parts = [f"{k}-{clean_label(entities[k])}" for k in ENTITY_ORDER if entities.get(k)]
    return "_".join(parts + [suffix])


@dataclass
class Output:
    """One NIfTI file produced by dcm2niix for a dataset, with its companions."""

    nifti: Path
    entities: dict[str, str] = field(default_factory=dict)  # echo / part / rec derived from the dcm2niix suffixes
    suffix: str | None = None  # overrides the dataset suffix (fmap magnitude/phase)


@dataclass
class Item:
    datatype: str
    suffix: str
    entities: dict[str, str]
    outputs: list[Output]
    order: int  # acquisition order, used for run numbering
    source: str = ""  # human-readable origin for the report
    fmap_kind: str | None = None


def _companions(nifti: Path) -> list[Path]:
    stem = nifti.name[: -len(".nii.gz")] if nifti.name.endswith(".nii.gz") else nifti.stem
    return [nifti.with_name(stem + ext) for ext in SIDECAR_EXTENSIONS if nifti.with_name(stem + ext).exists()]


def _output_suffix(item: Item, output: Output) -> str:
    if output.suffix:
        return output.suffix
    if item.datatype == "fmap" and item.fmap_kind == "b0":
        part = output.entities.get("part")
        echo = output.entities.get("echo")
        if part == "phase":
            return f"phase{echo}" if echo else "phasediff"
        # magnitudeN only when actually paired with a numbered echo (two-echo phasediff case); otherwise the
        # single unnumbered "magnitude" BIDS uses for the precomputed-fieldmap case (see convert.parse_outputs)
        return f"magnitude{echo}" if echo else "magnitude"
    return item.suffix


def _output_entities(item: Item, output: Output) -> dict[str, str]:
    entities = {**item.entities, **output.entities}
    if item.datatype == "fmap" and item.fmap_kind == "b0":
        entities.pop("part", None)
        entities.pop("echo", None)
    return entities


def build_session(items: list[Item], session_dir: Path, sub: str, ses: str, task_label: str) -> list[dict[str, Any]]:
    """Copy all outputs into ``session_dir`` under their final BIDS names; returns one record per NIfTI."""
    session_dir.mkdir(parents=True, exist_ok=True)
    planned: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: i.order):
        for output in item.outputs:
            entities = _output_entities(item, output)
            if item.datatype == "func":
                entities["task"] = task_label if entities.get("task", "unknown") == "unknown" else entities["task"]
            planned.append({"item": item, "output": output, "entities": entities, "suffix": _output_suffix(item, output)})

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for p in planned:
        key = (p["item"].datatype, p["suffix"], tuple(sorted(p["entities"].items())))
        groups.setdefault(key, []).append(p)
    for members in groups.values():
        if len(members) > 1:
            for n, p in enumerate(members, start=1):
                p["entities"] = {**p["entities"], "run": str(n)}

    records = []
    for p in planned:
        entities = {"sub": sub, "ses": ses, **p["entities"]}
        name = basename(entities, p["suffix"])
        target_dir = session_dir / p["item"].datatype
        target_dir.mkdir(parents=True, exist_ok=True)
        nifti: Path = p["output"].nifti
        shutil.copy2(nifti, target_dir / f"{name}.nii.gz")
        for companion in _companions(nifti):
            shutil.copy2(companion, target_dir / f"{name}{companion.suffix}")
        records.append({"name": name, "datatype": p["item"].datatype, "path": target_dir / f"{name}.nii.gz",
                        "item": p["item"], "task": entities.get("task"), "suffix": p["suffix"]})

    _finish_sidecars(records, sub, ses)
    return records


# The BIDS field-map suffixes that actually *are* the field map (as opposed to a magnitude reference image) --
# IntendedFor belongs on these, not on every file in the fmap/ folder.
FIELDMAP_SUFFIXES = {"phasediff", "fieldmap", "phase1", "phase2"}


def _finish_sidecars(records: list[dict[str, Any]], sub: str, ses: str) -> None:
    images = [f"ses-{ses}/{r['datatype']}/{r['name']}.nii.gz" for r in records if r["datatype"] != "fmap"]
    for r in records:
        sidecar = r["path"].with_name(r["name"] + ".json")
        if not sidecar.exists():
            continue
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        if r["datatype"] == "func":
            meta["TaskName"] = r["task"]
        if r["suffix"] == "fieldmap":
            meta["Units"] = "Hz"
        if r["datatype"] == "fmap" and r["suffix"] in FIELDMAP_SUFFIXES and images:
            meta["IntendedFor"] = images
        sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def replace_session(staged_root: Path, bids_root: Path, sub: str, ses: str) -> Path:
    """Swap the staged ``sub-x/ses-y`` folder into the dataset, replacing a previous conversion of the same exam."""
    source = staged_root / f"sub-{sub}" / f"ses-{ses}"
    target = bids_root / f"sub-{sub}" / f"ses-{ses}"
    if target.exists():
        shutil.rmtree(target)
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    return target


def ensure_dataset_description(bids_root: Path, name: str) -> None:
    path = bids_root / "dataset_description.json"
    if path.exists():
        return
    bids_root.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"Name": name, "BIDSVersion": BIDS_VERSION, "DatasetType": "raw",
             "GeneratedBy": [{"Name": "agora2bids", "Version": __version__}]},
            indent=2,
        ),
        encoding="utf-8",
    )
    readme = bids_root / "README"
    if not readme.exists():
        readme.write_text(f"{name}\n\nConverted from Agora with agora2bids.\n", encoding="utf-8")


PARTICIPANTS_JSON = {
    "age": {"Description": "Age at the time of the exam", "Units": "years"},
    "sex": {"Description": "Sex of the participant", "Levels": {"M": "male", "F": "female", "O": "other"}},
}


def age_at(birth_date: str | None, exam_date: str | None) -> str:
    def parse(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            return None

    born, exam = parse(birth_date), parse(exam_date)
    if not born or not exam or exam < born:
        return "n/a"
    years = exam.year - born.year - ((exam.month, exam.day) < (born.month, born.day))
    return str(years)


def upsert_participant(bids_root: Path, sub: str, sex: str | None, age: str) -> None:
    tsv = bids_root / "participants.tsv"
    rows: dict[str, dict[str, str]] = {}
    if tsv.exists():
        with tsv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                rows[row["participant_id"]] = row
    sex_code = {"m": "M", "f": "F", "o": "O"}.get((sex or "").lower(), "n/a")
    rows[f"sub-{sub}"] = {"participant_id": f"sub-{sub}", "age": age, "sex": sex_code}
    with tsv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["participant_id", "age", "sex"], delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow({k: rows[key].get(k, "n/a") for k in ("participant_id", "age", "sex")})
    pjson = bids_root / "participants.json"
    if not pjson.exists():
        pjson.write_text(json.dumps(PARTICIPANTS_JSON, indent=2), encoding="utf-8")
