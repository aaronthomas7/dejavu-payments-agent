"""Deterministic safety rules that run *after* the model, whatever the model said.

Hindsight directives already tell reflect() never to release sanctions holds.
This module is the belt to that pair of braces: plain code the model cannot
talk its way around.
"""

from __future__ import annotations

import re
from typing import Any

from .taxonomy import HUMAN_ONLY_CODES, UNKNOWN

# 8+ letters/digits with at least 6 digits: account numbers and IBANs, but not
# invoice numbers (KCS-55812), case IDs (EXC-260826-01) or payment refs (PAY-7637059).
_ACCOUNT_LIKE = re.compile(r"\b(?=[A-Z0-9]*\d[A-Z0-9]*\d[A-Z0-9]*\d[A-Z0-9]*\d[A-Z0-9]*\d[A-Z0-9]*\d)[A-Z0-9]{8,34}\b")

AUTO_FIX_MIN_CONFIDENCE = 0.8
NO_EVIDENCE_MAX_CONFIDENCE = 0.6

# Ignored when comparing beneficiary names, so "Desert Star General Trading LLC" matches "Desert Star General Trading".
_LEGAL_WORDS = {"llc", "ltd", "limited", "fze", "fzco", "fzc", "co", "company", "inc", "corp", "plc", "pte", "pvt",
                "private", "sa", "ag", "gmbh", "as", "jsc", "bv", "nv", "the"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _beneficiary(case: dict[str, Any]) -> str:
    return str(((case.get("payment") or {}).get("creditor") or {}).get("name") or "")


_LIST_ENTRY = re.compile(r"list entry\s*'([^']+)'", re.IGNORECASE)
_CLEARED = re.compile(r"false positive|cleared|clearance", re.IGNORECASE)


def _core(text: str) -> list[str]:
    words = _words(text)
    return [w for w in words if w not in _LEGAL_WORDS] or words


def _mentions(text: str, name: str) -> bool:
    """`name` appears in `text` as a whole phrase, ignoring case, punctuation and legal words like LLC."""
    phrase = " ".join(_core(name))
    return bool(phrase) and f" {phrase} " in f" {' '.join(_core(text))} "


def _listed_name(case: dict[str, Any]) -> str:
    m = _LIST_ENTRY.search(case.get("counterparty_message") or "")
    return m.group(1) if m else ""


def names_listed_entity(case: dict[str, Any]) -> bool:
    """True when the beneficiary carries the watch-list entry's own name (one name contains the other)."""
    benef, listed = set(_core(_beneficiary(case))), set(_core(_listed_name(case)))
    return bool(benef and listed) and (benef <= listed or listed <= benef)


def cleared_before(case: dict[str, Any], texts: list[str]) -> bool:
    """Memory names this exact beneficiary, and records a clearance for it or for the same watch-list entry."""
    who, listed = _beneficiary(case), _listed_name(case)
    if not any(_mentions(t, who) for t in texts):
        return False
    return any(_CLEARED.search(t) and (_mentions(t, who) or bool(listed and _mentions(t, listed))) for t in texts)


def _compliance_referral(case: dict[str, Any]) -> str:
    p = case.get("payment") or {}
    amount = p.get("amount")
    money = f"{p.get('currency', '')} {amount:,.2f}".strip() if isinstance(amount, (int, float)) else ""
    alert = (case.get("counterparty_message") or "").strip()
    return (f"Compliance team, please review payment {p.get('payment_ref') or case.get('case_id', '')}"
            f"{' of ' + money if money else ''} from {(p.get('debtor') or {}).get('name', 'our client')} "
            f"to {_beneficiary(case) or 'the beneficiary'}. {alert} "
            "We have no previous clearance on record for this beneficiary. The payment stays on hold until you decide.")


def mask_accounts(text: str) -> str:
    def _mask(m: re.Match) -> str:
        s = m.group(0)
        if sum(ch.isdigit() for ch in s) < 6:
            return s
        return "*" * (len(s) - 4) + s[-4:]

    return _ACCOUNT_LIKE.sub(_mask, text or "")


def apply(case: dict[str, Any], diag: dict[str, Any], used_memory: bool,
          recalled_texts: list[str] | None = None) -> dict[str, Any]:
    """Mutates and returns `diag`, adding requires_human_approval / auto_fix_eligible / guardrail_notes.

    `recalled_texts` are the memories recalled for this case (cited or not); the sanctions check reads them.
    """
    notes: list[str] = []
    rc = diag["root_cause"]
    is_screening = case.get("exception_type") == "SCREENING_HOLD"

    # 1) A sanctions-screening hold can only ever be classified as a sanctions cause.
    if is_screening and rc not in HUMAN_ONLY_CODES:
        notes.append(f"Screening hold cannot be '{rc}'; reclassified as SANCTIONS_POTENTIAL_MATCH for Compliance review.")
        rc = diag["root_cause"] = "SANCTIONS_POTENTIAL_MATCH"
        diag["confidence"] = min(float(diag.get("confidence", 0.5)), 0.5)

    # 1b) "Known false positive" needs proof: memory of this exact beneficiary and of a clearance. A new name that
    #     only looks like one cleared before (Red Sea Star vs Desert Star) is a new potential match.
    texts = [str(ev.get("text") or "") for ev in diag.get("evidence") or []] + [str(t) for t in recalled_texts or []]
    listed = names_listed_entity(case)
    if rc == "SANCTIONS_KNOWN_FALSE_POSITIVE" and (listed or not cleared_before(case, texts)):
        who = _beneficiary(case) or "this beneficiary"
        notes.append(f"{who} has the same name as the watch-list entry, so it can never be a known false positive."
                     if listed else f"No past clearance for {who} in memory, so DejaVu treats this as a new potential match.")
        rc = diag["root_cause"] = "SANCTIONS_POTENTIAL_MATCH"
        diag["confidence"] = min(float(diag.get("confidence") or 0.5), NO_EVIDENCE_MAX_CONFIDENCE)
        diag["evidence"] = []
        diag["reasoning"] = (f"The screening alert is for {who}, and memory has no past clearance for this beneficiary. "
                             "Clearances for other names that look similar do not carry over, so this needs a full "
                             "sanctions review.")
        diag["recommended_action"] = ("Route to Compliance for a full sanctions review. Keep the payment on hold "
                                      "until Compliance decides.")
        diag["draft_message"] = _compliance_referral(case)

    # 2) Sanctions causes: a human must decide, and the action must go to Compliance.
    human = rc in HUMAN_ONLY_CODES or is_screening or rc == UNKNOWN
    if rc in HUMAN_ONLY_CODES:
        notes.append("Sanctions rule: DejaVu never releases a screening hold. Routed to Compliance for a human decision.")
        if "compliance" not in (diag.get("recommended_action") or "").lower():
            diag["recommended_action"] = "Route to Compliance for review. " + (diag.get("recommended_action") or "")

    # 3) No evidence, no high confidence.
    conf = max(0.0, min(1.0, float(diag.get("confidence") or 0.0)))
    if (not used_memory or not diag.get("evidence")) and conf > NO_EVIDENCE_MAX_CONFIDENCE:
        notes.append(f"Confidence capped at {NO_EVIDENCE_MAX_CONFIDENCE:.0%}: no supporting evidence from memory.")
        conf = NO_EVIDENCE_MAX_CONFIDENCE
    diag["confidence"] = round(conf, 2)

    # 4) Never leak full account numbers in anything we show or send.
    for key in ("draft_message", "recommended_action", "reasoning", "prevention_tip"):
        if diag.get(key):
            diag[key] = mask_accounts(diag[key])

    diag["requires_human_approval"] = human
    diag["auto_fix_eligible"] = (not human) and conf >= AUTO_FIX_MIN_CONFIDENCE and rc != UNKNOWN
    diag["guardrail_notes"] = notes
    return diag
