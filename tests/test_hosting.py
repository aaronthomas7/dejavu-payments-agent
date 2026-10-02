"""A hosted copy starts with an empty bank and loses its database on every restart; both must be handled."""

import json
import time

from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.main import app


def _isolate(monkeypatch, tmp_data, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_data)
    monkeypatch.setattr(settings, "db_path", tmp_path / "hosted.db")


def _memory_ids(tmp_data):
    path = tmp_data / "local_memory.json"
    return [i["case_id"] for i in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


def _item(case_id):
    return {"id": f"local-{case_id}", "case_id": case_id, "text": f"Payment exception {case_id}.",
            "occurred": "2026-09-01T10:00:00+08:00", "root_cause": "CLIENT_TYPO_IN_DETAILS", "bank": "RHHB",
            "bank_name": "Rheinland Handelsbank", "code": "AC01", "tags": []}


def _wait_for_seeding(client):
    for _ in range(400):
        seeding = client.get("/api/status").json()["seeding"]
        if seeding and not seeding["running"]:
            return seeding
        time.sleep(0.05)
    raise AssertionError("seeding did not finish")


def test_a_hosted_copy_loads_the_history_into_its_empty_bank(monkeypatch, tmp_data, tmp_path):
    _isolate(monkeypatch, tmp_data, tmp_path)
    monkeypatch.setattr(settings, "seed_history", True)
    history = json.loads((tmp_data / "history.json").read_text(encoding="utf-8"))
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True}
        seeding = _wait_for_seeding(c)
    assert seeding == {"done": len(history), "total": len(history), "running": False, "error": None}
    assert sorted(_memory_ids(tmp_data)) == sorted(c["case_id"] for c in history)

    with TestClient(app) as c:  # a restart finds nothing left to load
        assert _wait_for_seeding(c)["total"] == 0


def test_lessons_from_cases_the_desk_no_longer_has_are_forgotten(monkeypatch, tmp_data, tmp_path):
    _isolate(monkeypatch, tmp_data, tmp_path)
    live = json.loads((tmp_data / "live.json").read_text(encoding="utf-8"))
    history = json.loads((tmp_data / "history.json").read_text(encoding="utf-8"))
    # Left over from before a restart: a simulated payment the new database never saw, and a demo case that is open.
    stale = [_item("EXC-260101-S07"), _item(live[0]["case_id"]), _item(history[0]["case_id"])]
    (tmp_data / "local_memory.json").write_text(json.dumps(stale), encoding="utf-8")

    with TestClient(app) as c:
        assert _memory_ids(tmp_data) == [history[0]["case_id"]]  # history stays, the leftovers are gone

        main.STATE["memory"].items.append(_item("EXC-260102-S03"))  # a stray lesson appearing later
        reset = c.post("/api/admin/reset-demo").json()
        assert reset["forgotten_in_memory"] == 1
        assert _memory_ids(tmp_data) == [history[0]["case_id"]]
