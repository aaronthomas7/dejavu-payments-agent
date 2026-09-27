from fastapi.testclient import TestClient

from app.main import app


def test_desk_flow_end_to_end():
    with TestClient(app) as c:
        st = c.get("/api/status").json()
        assert st["offline"] is True and st["cases"]["open"] == 10

        open_cases = c.get("/api/cases?status=open").json()
        assert all("ground_truth" not in x for x in open_cases)
        case = next(x for x in open_cases if x["payment"]["creditor_bank"]["code"] == "PDCB")

        d = c.post(f"/api/cases/{case['case_id']}/diagnose", json={"use_memory": True})
        assert d.status_code == 200 and d.json()["case_id"] == case["case_id"]

        bad = c.post(f"/api/cases/{case['case_id']}/resolve", json={"root_cause": "NOT_A_CAUSE", "note": "abc"})
        assert bad.status_code == 422

        ok = c.post(f"/api/cases/{case['case_id']}/resolve",
                    json={"root_cause": "ACCOUNT_FORMAT_CHANGED", "note": "prefixed 601",
                          "agent_root_cause": d.json()["root_cause"], "agent_confidence": d.json()["confidence"]})
        assert ok.status_code == 200 and ok.json()["retained"] is True

        again = c.post(f"/api/cases/{case['case_id']}/resolve", json={"root_cause": "ACCOUNT_FORMAT_CHANGED", "note": "x" * 5})
        assert again.status_code == 409

        assert c.get("/api/cases/EXC-000000-00").status_code == 404
        assert c.post("/api/admin/reset-demo").json()["reopened"] >= 1
        assert c.get("/api/replay").json()["available"] in (True, False)
        assert len(c.get("/api/taxonomy").json()) == 15


def test_history_cases_show_their_recorded_resolution():
    with TestClient(app) as c:
        resolved = c.get("/api/cases?status=resolved").json()
        hist = [x for x in resolved if x["source"] == "history"]
        assert hist and all(x["resolution"]["historical"] for x in hist)
