"""Philips GOAL/DB parameter pool with enum decoding.

Agora stores each Philips parameter set as a flat list of ``{"Name", "Value", "Properties"}`` records. Enum
parameters hold the raw integer; the table that maps integers to the stable Philips code (for example
``MGUACQ_SCT_SPECTRO``) is in the parameter set's ``EnumDescriptions`` and referenced by
``Properties.EnumDescription``. The classifier compares codes, which are identical across software versions.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Used only when a parameter set carries no EnumDescriptions. Taken from the Agora GOAL export (release 11).
FALLBACK_ENUMS: dict[str, dict[int, str]] = {
    "EX_ACQ_scan_type": {0: "MGUACQ_SCT_IMAGING", 1: "MGUACQ_SCT_SPECTRO"},
    "EX_DIFF_enable": {0: "MGU_DIFF_TECH_NO", 1: "MGU_DIFF_TECH_DWI", 2: "MGU_DIFF_TECH_DTI"},
    "EX_FLL_mode": {
        0: "MGUFLL_SEL_NO", 1: "MGUFLL_SEL_FAIR", 2: "MGUFLL_SEL_TILT", 3: "MGUFLL_SEL_CASL",
        4: "MGUFLL_SEL_STAR", 5: "MGUFLL_SEL_pCASL", 6: "MGUFLL_SEL_TIMESLIP", 7: "MGUFLL_SEL_CINEMA",
    },
    "EX_ACQ_B0_map": {
        0: "MGUACQ_B0_MAP_NO", 1: "MGUACQ_B0_MAP_YES", 2: "MGUACQ_B0_MAP_CAL", 3: "MGUACQ_B0_MAP_PRE_SCAN",
    },
    "EX_ACQ_B1_map": {0: "MGUACQ_B1_MAP_NO", 1: "MGUACQ_B1_MAP_YES", 2: "MGUACQ_B1_MAP_CAL"},
    "EX_ACQ_fast_imaging_mode": {
        0: "MGUACQ_FAST_NO", 1: "MGUACQ_FAST_TSE", 2: "MGUACQ_FAST_TFE", 3: "MGUACQ_FAST_EPI",
        4: "MGUACQ_FAST_GRASE", 5: "MGUACQ_FAST_TFE_EPI", 6: "MGUACQ_FAST_TSI",
    },
    "EX_ACQ_imaging_sequence": {
        0: "MGUACQ_SEQ_SE", 1: "MGUACQ_SEQ_IR", 2: "MGUACQ_SEQ_MIXED", 3: "MGUACQ_SEQ_FFE",
        4: "MGUACQ_SEQ_ECHO", 5: "MGUACQ_SEQ_FID",
    },
    "EX_DYN_study": {0: "MPUDYN_MODE_NO", 1: "MPUDYN_MODE_INDIVIDUAL", 2: "MPUDYN_MODE_BLOCK"},
    "EX_PC_angio_mode": {
        0: "MPU_PC_ANGIO_NO", 1: "MPU_PC_ANGIO_INFLOW", 2: "MPU_PC_ANGIO_PC", 3: "MPU_PC_ANGIO_CE",
    },
    "EX_PC_quant_flow": {0: "NO", 1: "YES"},
    "EX_ACQ_dixon": {0: "MPUACQ_DIXON_NO", 1: "MPUACQ_DIXON_YES", 2: "MPUACQ_DIXON_QUANT"},
    "EX_PROC_image_types": {
        0: "MGU_ITYP_REAL", 1: "MGU_ITYP_IMAGINARY", 2: "MGU_ITYP_MODULUS", 3: "MGU_ITYP_PHASE", 4: "MGU_ITYP_NO",
    },
    "EX_ACQ_smartscout_type": {0: "MPUACQ_SMARTPLAN_TYPE_NONE"},
}


class ParamPool:
    """Name-indexed Philips parameters with code decoding."""

    def __init__(self, records: Iterable[dict[str, Any]] = (), enums: dict[int, dict[int, str]] | None = None):
        self._by_name: dict[str, dict[str, Any]] = {}
        self._enums = enums or {}
        for record in records:
            name = record.get("Name")
            if name is not None:
                self._by_name[name] = record

    @classmethod
    def from_parametersets(cls, parametersets: Iterable[Any]) -> ParamPool:
        """Build from gtagora ``ParameterSet`` objects (``.parameters`` list of dicts, optional ``.properties``)."""
        records: list[dict[str, Any]] = []
        enums: dict[int, dict[int, str]] = {}
        for pset in parametersets:
            records.extend(getattr(pset, "parameters", None) or [])
            _read_enums(getattr(pset, "properties", None) or {}, enums)
        return cls(records, enums)

    @classmethod
    def from_file(cls, path: str | Path) -> ParamPool:
        """Load a parameter export: a flat record list or an Agora task ``parameters.json`` (list of sets)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        enums: dict[int, dict[int, str]] = {}
        if data and all(isinstance(item, dict) and "parameters" in item and "Name" not in item for item in data):
            for pset in data:
                records.extend(pset.get("parameters") or [])
                _read_enums(pset.get("properties") or {}, enums)
        else:
            records = data
        return cls(records, enums)

    def __len__(self) -> int:
        return len(self._by_name)

    def has(self, name: str) -> bool:
        return name in self._by_name

    def records(self) -> list[dict[str, Any]]:
        return list(self._by_name.values())

    def value(self, name: str, default: Any = None) -> Any:
        record = self._by_name.get(name)
        return record.get("Value", default) if record is not None else default

    def number(self, name: str) -> float | None:
        v = self.value(name)
        if isinstance(v, bool) or v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def code(self, name: str) -> Any:
        """Decoded enum code (str, or list of str for array enums); ``None`` if absent or undecodable."""
        record = self._by_name.get(name)
        if record is None:
            return None
        value = record.get("Value")
        table = self._enums.get((record.get("Properties") or {}).get("EnumDescription"))
        if isinstance(value, list):
            return [self._decode(name, v, table) for v in value]
        return self._decode(name, value, table)

    @staticmethod
    def _decode(name: str, value: Any, table: dict[int, str] | None) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if table and value in table:
            return table[value]
        fallback = FALLBACK_ENUMS.get(name, {})
        if value in fallback:
            return fallback[value]
        if name == "EX_ACQ_smartscout_type" and value != 0:
            return "MPUACQ_SMARTPLAN_TYPE_OTHER"
        return None


def _read_enums(properties: dict[str, Any], into: dict[int, dict[int, str]]) -> None:
    for entry in properties.get("EnumDescriptions") or []:
        into[entry["ID"]] = {v["ValueInt"]: v["Value"] for v in entry.get("Values", [])}
