"""Conversion report."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Entry:
    series: str
    dataset_id: int
    kind: str
    status: str  # converted | excluded | failed | would-convert (dry run)
    reason: str = ""
    datatype: str | None = None
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def write_report(bids_root: Path, exam_id: int, sub: str, ses: str, entries: list[Entry]) -> Path:
    directory = bids_root / "code" / "agora2bids"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report_sub-{sub}_ses-{ses}_exam-{exam_id}.json"
    path.write_text(json.dumps([asdict(e) for e in entries], indent=2), encoding="utf-8")
    return path


def summary(entries: list[Entry]) -> str:
    lines = []
    for e in entries:
        detail = f"{e.datatype}: {', '.join(e.files)}" if e.status in ("converted", "would-convert") and e.files else e.reason
        if e.status == "would-convert" and e.datatype and not e.files:
            detail = e.datatype
        lines.append(f"  {e.status:13s} [{e.dataset_id}] {e.series}  ({e.kind}) {detail}")
        lines.extend(f"                    ! {w}" for w in e.warnings)
    return "\n".join(lines)
