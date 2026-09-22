from types import SimpleNamespace

import pytest

from agora2bids.agora_source import patient_info

PATIENT = {"id": 12, "name": "Jane Doe", "patient_id": "HOSP2", "birth_date": "1980-12-05", "sex": "f", "weight": 60}


class FakeAgora:
    http_client = None

    def __init__(self):
        self.requested = []

    def get_patient(self, patient_id):
        self.requested.append(patient_id)
        return SimpleNamespace(id=patient_id, sex="m", birth_date="1970-01-01")


def test_nested_patient_object_is_used_without_extra_request():
    agora = FakeAgora()
    info = patient_info(SimpleNamespace(id=1, patient=dict(PATIENT)), agora)
    assert (info.id, info.sex, info.birth_date) == (12, "f", "1980-12-05")
    assert agora.requested == []


def test_bare_patient_id_is_resolved():
    agora = FakeAgora()
    info = patient_info(SimpleNamespace(id=1, patient=7), agora)
    assert (info.id, info.sex) == (7, "m")
    assert agora.requested == [7]


def test_exam_without_patient_gives_a_clear_error():
    with pytest.raises(ValueError, match="no patient"):
        patient_info(SimpleNamespace(id=5, patient=None), FakeAgora())
