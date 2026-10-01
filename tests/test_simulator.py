import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import SimSendRequest
from app.prompts import case_summary
from app.simulator import PaymentNetwork

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def net():
    entities = json.loads((ROOT / "data" / "entities.json").read_text(encoding="utf-8"))
    return PaymentNetwork(ROOT / "data" / "network_rules.json", entities)


def pay(**kw):
    base = dict(client="Arcadia Foods Pte Ltd", beneficiary="Hussain Sagar Spices Private Limited",
                beneficiary_bank="Charminar Co-operative Bank", currency="INR", amount=1240000,
                submit_time_sgt="10:00", submit_date="2026-10-03", beneficiary_account="40218877316",
                remittance_info="INV HSS-2207 spices")
    base.update(kw)
    return SimSendRequest(**base)


@pytest.mark.parametrize("kw, status, code", [
    ({}, "rejected", "AC01"),                                                     # old 11-digit account
    ({"beneficiary_account": "50040218877316"}, "credited", None),                # new 14-digit account
    ({"beneficiary_bank": "Victoria Harbour Bank", "currency": "USD", "amount": 18000, "remittance_info": "Components",
      "beneficiary_account": "719-384955-324"}, "rejected", "RR04"),
    ({"beneficiary_bank": "Victoria Harbour Bank", "currency": "USD", "amount": 18000,
      "remittance_info": "INV KCS-55812 components", "beneficiary_account": "719-384955-324"}, "credited", None),
    ({"beneficiary_bank": "Victoria Harbour Bank", "currency": "USD", "amount": 4000, "remittance_info": "Samples",
      "beneficiary_account": "719-384955-324"}, "credited", None),                # below the invoice threshold
    ({"beneficiary_bank": "Nordkyst Bank", "currency": "USD", "amount": 27500, "beneficiary_account": "NO9357313943399",
      "intermediary": "Atlantic Clearing Bank, New York"}, "rejected", "RC01"),
    ({"beneficiary_bank": "Nordkyst Bank", "currency": "USD", "amount": 27500, "beneficiary_account": "NO9357313943399",
      "intermediary": "Hudson Federal Bank, New York"}, "credited", None),
    ({"beneficiary_bank": "Tungabhadra Co-operative Bank", "beneficiary": "Deccan Bio Labs Pvt Ltd",
      "beneficiary_account": "11834713830157"}, "rejected", "BE01"),
    ({"beneficiary_bank": "Tungabhadra Co-operative Bank", "beneficiary": "Deccan Bio Labs Private Limited",
      "beneficiary_account": "11834713830157"}, "credited", None),
    ({"beneficiary_bank": "Golconda Commercial Bank", "submit_time_sgt": "15:10",
      "beneficiary_account": "502002863428720"}, "credited_next_day", None),
    ({"beneficiary_bank": "Golconda Commercial Bank", "submit_time_sgt": "11:00",
      "beneficiary_account": "502002863428720"}, "credited", None),
    ({"beneficiary_bank": "Desert Rose Bank", "beneficiary": "Desert Star General Trading LLC", "currency": "USD",
      "beneficiary_account": "AE070339586728147928393"}, "held", None),
    ({"beneficiary_bank": "Rheinland Handelsbank", "currency": "EUR", "beneficiary_account": "DE89370400440532013000"},
     "credited", None),
    ({"beneficiary_bank": "Nowhere Bank"}, "rejected", "RC01"),
])
def test_network_rules(net, kw, status, code):
    outcome = net.evaluate(pay(**kw))
    assert outcome.status == status
    assert outcome.code == code


def test_rejection_becomes_a_normal_case_without_leaking_the_rule(net):
    req = pay()
    case = net.build_case(req, net.evaluate(req), "EXC-261003-S01", "PAY-1234567")
    assert case["exception_type"] == "REJECTED" and case["reason_code"] == "AC01"
    assert case["payment"]["creditor_bank"]["code"] == "CHMR"
    assert case["payment"]["debtor"]["client_id"] == "arcadia"
    summary = case_summary(case)  # what the model sees
    assert "40218877316 (11 digits)" in summary
    assert "14-digit" not in summary and "ACCOUNT_FORMAT_CHANGED" not in summary


def test_directory_hides_the_rules(net):
    d = net.directory()
    assert any(b["name"] == "Charminar Co-operative Bank" for b in d["banks"])
    assert "rules" not in json.dumps(d)
    assert any("14-digit" in r for b in net.rulebook()["banks"] for r in b["rules"])


def test_send_open_diagnose_learn_and_reset():
    with TestClient(app) as c:
        open_before = c.get("/api/status").json()["cases"]["open"]
        body = pay().model_dump()
        r = c.post("/api/sim/send", json=body)
        assert r.status_code == 200
        res = r.json()
        assert res["status"] == "rejected" and res["iso_status"] == "RJCT" and res["case_id"].startswith("EXC-")
        assert c.get("/api/status").json()["cases"]["open"] == open_before + 1

        case = c.get(f"/api/cases/{res['case_id']}").json()
        assert "ground_truth" not in case and case["source"] == "sim"

        d = c.post(f"/api/cases/{res['case_id']}/diagnose", json={"use_memory": True})
        assert d.status_code == 200
        ok = c.post(f"/api/cases/{res['case_id']}/resolve",
                    json={"root_cause": "ACCOUNT_FORMAT_CHANGED", "note": "Charminar moved to 14-digit account numbers."})
        assert ok.json()["retained"] is True

        fixed = c.post("/api/sim/send", json={**body, "beneficiary_account": "50040218877316"}).json()
        assert fixed["status"] == "credited" and fixed["case_id"] is None

        second = c.post("/api/sim/send", json=body).json()
        assert second["case_id"] != res["case_id"]

        reset = c.post("/api/admin/reset-demo").json()
        assert reset["simulated_removed"] == 2
        assert c.get(f"/api/cases/{res['case_id']}").status_code == 404
        assert c.get("/api/status").json()["cases"]["open"] == open_before

        assert c.post("/api/sim/send", json={**body, "beneficiary_account": "12"}).status_code == 422
        assert "banks" in c.get("/api/sim/network").json()
        assert "banks" in c.get("/api/sim/rules").json()
