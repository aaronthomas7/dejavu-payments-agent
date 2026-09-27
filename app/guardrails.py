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


def mask_accounts(text: str) -> str:
    def _mask(m: re.Match) -> str:
        s = m.group(0)
        if sum(ch.isdigit() for ch in s) < 6:
            return s
        return "*" * (len(s) - 4) + s[-4:]

    return _ACCOUNT_LIKE.sub(_mask, text or "")


def apply(case: dict[str, Any], diag: dict[str, Any], used_memory: bool) -> dict[str, Any]:
    """Mutates and returns `diag`, adding requires_human_approval / auto_fix_eligible / guardrail_notes."""
    notes: list[str] = []
    rc = diag["root_cause"]
    is_screening = case.get("exception_type") == "SCREENING_HOLD"

    # 1) A sanctions-screening hold can only ever be classified as a sanctions cause.
    if is_screening and rc not in HUMAN_ONLY_CODES:
        notes.append(f"Screening hold cannot be '{rc}'; reclassified as SANCTIONS_POTENTIAL_MATCH for Compliance review.")
        rc = diag["root_cause"] = "SANCTIONS_POTENTIAL_MATCH"
        diag["confidence"] = min(float(diag.get("confidence", 0.5)), 0.5)

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
