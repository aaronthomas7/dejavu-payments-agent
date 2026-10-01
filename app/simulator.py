"""Payment network simulator, used for live demos.

Fictional banks with their own hidden rules (data/network_rules.json) stand in for the real payment network.
When the desk sends a payment, the simulator replies the way a bank would (an ISO 20022 pacs.002 status), and a
rejection or hold becomes a normal exception on the desk.

DejaVu's agent never reads these rules. Like an ops analyst, it only sees the message the bank sends back, so
anything it knows about a bank it has to learn from cases the desk resolves.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SGT = timezone(timedelta(hours=8))
CHANNEL = "SWIFT pacs.008 (ISO 20022)"
ISO_STATUS = {"credited": "ACSC", "credited_next_day": "ACSP", "rejected": "RJCT", "held": "PDNG"}


@dataclass
class Outcome:
    status: str                        # credited | credited_next_day | rejected | held
    message: str
    rule_id: Optional[str] = None
    code: Optional[str] = None         # ISO reason code, e.g. AC01
    text: Optional[str] = None         # reason text, e.g. "Incorrect account number"
    exception_type: Optional[str] = None
    root_cause: Optional[str] = None   # hidden truth, used only as the presenter's correction hint
    analyst_note: Optional[str] = None

    @property
    def opens_exception(self) -> bool:
        return self.status in ("rejected", "held")


def _digits(value: str) -> str:
    return "".join(ch for ch in value or "" if ch.isdigit())


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").lower().split())


class PaymentNetwork:
    def __init__(self, rules_path: Path, entities: dict[str, Any]):
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        self.about: str = data.get("about", "")
        self._banks: list[dict[str, Any]] = data["banks"]
        self._by_name = {_norm(b["name"]): b for b in self._banks}
        self._clients = {_norm(c["name"]): {"client_id": cid, **c} for cid, c in entities.get("clients", {}).items()}
        self._inters = {_norm(i["name"]): {"code": code, **i} for code, i in entities.get("intermediaries", {}).items()}

    # ---- what the desk can see -------------------------------------------------
    def directory(self) -> dict[str, Any]:
        """Banks, clients and intermediaries for the send form. Never includes the rules."""
        return {
            "banks": [{k: b[k] for k in ("code", "name", "bic", "city", "country")} for b in self._banks],
            "clients": [{"client_id": c["client_id"], "name": c["name"]} for c in self._clients.values()],
            "intermediaries": [{"code": i["code"], "name": i["name"], "bic": i["bic"]} for i in self._inters.values()],
        }

    def rulebook(self) -> dict[str, Any]:
        """The hidden rules, for the presenter to reveal after the demo."""
        return {"about": self.about, "banks": [
            {"name": b["name"], "rules": [r["summary"] for r in b.get("rules", [])]} for b in self._banks]}

    def bank(self, name: str) -> Optional[dict[str, Any]]:
        return self._by_name.get(_norm(name))

    # ---- the network's decision ------------------------------------------------
    def evaluate(self, req: Any) -> Outcome:
        bank = self.bank(req.beneficiary_bank)
        if not bank:
            return Outcome("rejected", f"Unknown beneficiary bank '{req.beneficiary_bank}'. Payment could not be routed.",
                           code="RC01", text="Bank identifier incorrect", exception_type="REJECTED")
        for rule in bank.get("rules", []):
            if self._breaks(rule["check"], req):
                o = rule["outcome"]
                status = o["status"]
                return Outcome(
                    status=status,
                    message=o["message"].format(account=req.beneficiary_account or ""),
                    rule_id=rule["id"], code=o.get("code"), text=o.get("text"),
                    exception_type=o.get("exception_type") or ("REJECTED" if status == "rejected" else None),
                    root_cause=rule.get("root_cause"), analyst_note=rule.get("analyst_note"),
                )
        return Outcome("credited", f"Credited to the beneficiary's account at {bank['name']}.")

    @staticmethod
    def _breaks(check: dict[str, Any], req: Any) -> bool:
        kind = check["type"]
        ccy_ok = req.currency in check.get("currencies", [req.currency])
        if kind == "account_digits":
            return len(_digits(req.beneficiary_account)) != int(check["digits"])
        if kind == "remittance_contains":
            if not ccy_ok or req.amount < float(check.get("min_amount", 0)):
                return False
            return not re.search(check["pattern"], req.remittance_info or "", flags=re.IGNORECASE)
        if kind == "intermediary_not":
            return ccy_ok and _norm(req.intermediary) in {_norm(n) for n in check["names"]}
        if kind == "name_excludes":
            return any(_norm(t) in _norm(req.beneficiary) for t in check["terms"])
        if kind == "name_includes":
            return any(_norm(t) in _norm(req.beneficiary) for t in check["terms"])
        if kind == "after_cutoff":
            return (req.submit_time_sgt or "00:00") > check["time_sgt"]
        raise ValueError(f"Unknown rule type: {kind}")

    # ---- turning a rejection into a desk exception ------------------------------
    def build_case(self, req: Any, outcome: Outcome, case_id: str, payment_ref: str,
                   now: Optional[datetime] = None) -> dict[str, Any]:
        now = (now or datetime.now(SGT)).astimezone(SGT).replace(microsecond=0)
        bank = self.bank(req.beneficiary_bank) or {"code": "UNKN", "name": req.beneficiary_bank, "bic": "UNKNXXXXXXX",
                                                   "city": "", "country": ""}
        client = self._clients.get(_norm(req.client)) or {
            "client_id": re.sub(r"[^a-z0-9]+", "-", _norm(req.client)).strip("-") or "client", "name": req.client,
            "account": ""}
        inter = self._inters.get(_norm(req.intermediary)) if req.intermediary else None
        submitted = _submitted_at(req, now)
        created = now if submitted <= now else submitted + timedelta(minutes=1)
        case = {
            "case_id": case_id,
            "created_at": created.isoformat(),
            "status": "open",
            "exception_type": outcome.exception_type or "REJECTED",
            "reason_code": outcome.code,
            "reason_text": outcome.text,
            "counterparty_message": outcome.message,
            "tracker": None,
            "debtor_balance_check": None,
            "payment": {
                "payment_ref": payment_ref,
                "channel": CHANNEL,
                "submitted_at": submitted.isoformat(),
                "value_date": submitted.date().isoformat(),
                "amount": float(req.amount),
                "currency": req.currency,
                "debtor": {"name": client["name"], "account": client.get("account", ""), "client_id": client["client_id"]},
                "creditor": {"name": req.beneficiary, "account": req.beneficiary_account or "", "country": bank["country"]},
                "creditor_bank": {k: bank[k] for k in ("name", "bic", "city", "country", "code")},
                "intermediary_bank": {"name": inter["name"], "bic": inter["bic"]} if inter else None,
                "remittance_info": req.remittance_info or "",
            },
            # The hidden truth stays on the server. It is only used to pre-fill the presenter's correction form.
            "ground_truth": {"root_cause": outcome.root_cause, "pattern_id": outcome.rule_id,
                             "resolution_note": outcome.analyst_note, "minutes_spent": None, "is_repeat": False},
            "simulated": True,
        }
        return case


def _submitted_at(req: Any, now: datetime) -> datetime:
    try:
        day = date.fromisoformat(req.submit_date) if req.submit_date else now.date()
        hh, mm = (int(x) for x in (req.submit_time_sgt or "").split(":")[:2])
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=SGT)
    except (ValueError, TypeError):
        return now


def new_payment_ref() -> str:
    return f"PAY-{random.randint(1_000_000, 9_999_999)}"


def case_id_prefix(now: Optional[datetime] = None) -> str:
    now = (now or datetime.now(SGT)).astimezone(SGT)
    return f"EXC-{now:%y%m%d}-S"
