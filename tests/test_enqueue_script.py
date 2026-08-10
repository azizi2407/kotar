"""scripts/enqueue_job.py — genel enqueue script'inin test edilebilir çekirdeği.

Gerçek DB round-trip (conftest sqlite + app_context autouse); mock yok.
"""
import json

import pytest

from extensions import db
from models import Job
from scripts.enqueue_job import run


def test_run_dogru_tip_ve_payload_ile_job_olusturur(client):
    j = run("brief", payload_json=json.dumps({"week_iso": "2026-W29"}), created_by="cli")
    assert j.id > 0
    assert j.type == "brief"
    assert j.status == "queued"
    assert j.payload == {"week_iso": "2026-W29"}
    assert j.created_by == "cli"

    # DB'de gerçekten var mı — round-trip
    row = db.session.get(Job, j.id)
    assert row is not None and row.type == "brief"


def test_run_payload_yoksa_bos_dict(client):
    j = run("ops_digest")
    assert j.payload == {}


def test_run_bos_string_payload_bos_dict(client):
    j = run("special_days", payload_json="")
    assert j.payload == {}


def test_run_gecersiz_json_hata_verir(client):
    with pytest.raises(ValueError):
        run("brief", payload_json="{gecersiz-json")


def test_run_created_by_opsiyonel(client):
    j = run("brief")
    assert j.created_by is None
