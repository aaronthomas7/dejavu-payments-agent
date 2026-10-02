"""Every piece of instruction text DejaVu sends to a model or to Hindsight, in one place."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .taxonomy import CODES, taxonomy_for_prompt

# ---------------------------------------------------------------------------
# Hindsight bank configuration
# ---------------------------------------------------------------------------

BANK_NAME = "DejaVu - Payment Exceptions Desk"

BANK_BACKGROUND = (
    "This memory bank belongs to the payment-operations desk of a bank's Singapore hub. The desk handles "
    "outgoing cross-border payments for corporate clients and investigates exceptions: rejections, returns, "
    "holds, sanctions-screening hits and beneficiary non-receipt claims. All times are Singapore time (SGT)."
)

RETAIN_MISSION = (
    "Extract durable, reusable operational knowledge from payment-exception cases: which beneficiary bank, "
    "client, beneficiary, currency, reason code and intermediary were involved; the root cause; the exact fix "
    "that worked (formats, prefixes, codes, correct names, new routing); conditions that matter (submission "
    "time vs cut-off, day of week, month-end, amount thresholds, the date a rule started); clearance or "
    "reference numbers; and whether DejaVu's own diagnosis was right or wrong and why."
)

REFLECT_MISSION = (
    "You are DejaVu, a senior payment-operations investigator. You remember every payment exception this desk "
    "has resolved and you use that history to diagnose new exceptions quickly, cite the past cases your advice "
    "is based on, and warn about payments that are likely to fail. Prefer specific, evidence-backed guidance "
    "over generic advice, and say clearly when memory has no relevant evidence."
)

DISPOSITION = {"disposition_skepticism": 4, "disposition_literalism": 4, "disposition_empathy": 2}

DIRECTIVES = [
    {
        "name": "Sanctions holds are human-only",
        "content": (
            "Never recommend releasing, auto-approving or bypassing a payment held by sanctions screening, even if "
            "the beneficiary was cleared before. Always route sanctions hits to the Compliance team for human "
            "review; a prior clearance reference may be cited only as supporting context."
        ),
        "priority": 100,
    },
    {
        "name": "Mask account numbers",
        "content": "Never write a full bank account number or IBAN in an answer. Show only the last 4 characters, e.g. ****0913.",
        "priority": 50,
    },
    {
        "name": "Cite evidence",
        "content": (
            "When you state a pattern, cite the dates or case IDs of the past exceptions it is based on. If memory "
            "has no relevant evidence, say so explicitly instead of guessing."
        ),
        "priority": 40,
    },
]

PLAYBOOK_ID = "exception-playbook"
PLAYBOOK_NAME = "Payment Exception Playbook"
PLAYBOOK_QUERY = (
    "Write the desk's playbook of recurring payment-exception patterns. For each pattern give: the trigger "
    "(beneficiary bank or client, reason code, and conditions such as submission time, day of week, month-end, "
    "amount or the date a rule changed), the root cause, the fix that worked, how to prevent it, how many times "
    "it has been seen and when it was last seen. Only include patterns supported by at least two cases. Add a "
    "short 'Changed over time' section for rules that started or changed on a specific date. Use markdown "
    "headings and bullet points; keep it concise."
)

# ---------------------------------------------------------------------------
# Diagnosis (Groq)
# ---------------------------------------------------------------------------

DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "enum": CODES},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"memory_ref": {"type": "string"}, "why_relevant": {"type": "string"}},
                "required": ["memory_ref", "why_relevant"],
                "additionalProperties": False,
            },
        },
        "recommended_action": {"type": "string"},
        "draft_message": {"type": "string"},
        "prevention_tip": {"type": "string"},
    },
    "required": ["root_cause", "confidence", "reasoning", "evidence", "recommended_action", "draft_message",
                 "prevention_tip"],
    "additionalProperties": False,
}

DIAGNOSIS_SYSTEM = f"""You are DejaVu, a senior payment-operations investigator at a bank.
You diagnose one payment exception at a time and pick exactly ONE root cause from this list:
{taxonomy_for_prompt()}

How to decide:
- If a MEMORY section is provided, it holds past cases and lessons from THIS desk. Apply a past pattern only when
  the key facts match: same beneficiary bank (or same client/beneficiary), same kind of error, and the same
  conditions (e.g. submission time vs a cut-off, day of week, month-end, amount threshold, whether the date is
  after a rule changed). If the facts differ, do not force the pattern.
- If there is no relevant memory, use general payments knowledge and keep confidence at or below 0.6.
- Sanctions-screening hits are never released by you: recommend routing to Compliance (cite any prior clearance).
- confidence is a number from 0 to 1.
- evidence lists the memory refs (e.g. "M2") you relied on; use an empty list if none.
- reasoning: 2-4 plain-English sentences a busy analyst can read in 10 seconds.
- recommended_action: concrete next steps for the analyst.
- draft_message: a short message to the client or counterparty bank. Mask account numbers (show last 4 only).
- prevention_tip: one sentence on how to stop this happening again.
Return JSON only."""


def fmt_time(iso: str | None) -> str:
    if not iso:
        return "unknown"
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b %Y %H:%M SGT")


def case_summary(case: dict[str, Any]) -> str:
    """The case as the analyst sees it. Never includes ground truth."""
    p = case["payment"]
    lines = [
        f"Case {case['case_id']} opened {fmt_time(case['created_at'])}",
        f"Exception: {case['exception_type']}"
        + (f" | reason {case['reason_code']} ({case['reason_text']})" if case.get("reason_code") else ""),
        f"Message: {case['counterparty_message']}",
    ]
    if case.get("tracker"):
        lines.append(case["tracker"])
    if case.get("debtor_balance_check"):
        lines.append(f"Client balance check: {case['debtor_balance_check']}")
    lines += [
        f"Payment {p['payment_ref']}: {p['currency']} {p['amount']:,.2f} via {p['channel']}",
        f"Submitted: {fmt_time(p['submitted_at'])}; value date {p['value_date']}",
        f"Client (debtor): {p['debtor']['name']}",
        f"Beneficiary: {p['creditor']['name']}, account {p['creditor']['account']} ({len(_digits(p['creditor']['account']))} digits)",
        f"Beneficiary bank: {p['creditor_bank']['name']} ({p['creditor_bank']['bic']}), {p['creditor_bank']['city']}, {p['creditor_bank']['country']}",
    ]
    if p.get("intermediary_bank"):
        lines.append(f"Intermediary: {p['intermediary_bank']['name']} ({p['intermediary_bank']['bic']})")
    lines.append(f"Remittance info: \"{p.get('remittance_info', '')}\"")
    return "\n".join(lines)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def recall_query(case: dict[str, Any]) -> str:
    p = case["payment"]
    bank = p["creditor_bank"]
    code = case.get("reason_code") or ""
    reason = case.get("reason_text") or ""
    submitted = datetime.fromisoformat(p["submitted_at"]).strftime("%A %H:%M SGT")
    return (
        f"{case['exception_type'].replace('_', ' ').lower()} {code} {reason} at {bank['name']} ({bank['bic'][:8]}) "
        f"for client {p['debtor']['name']} paying {p['creditor']['name']}; {p['currency']} {p['amount']:,.0f}; "
        f"submitted {submitted}. {case['counterparty_message'][:180]} How were similar exceptions resolved before?"
    )


def memory_block(memories: list[Any]) -> str:
    if not memories:
        return "MEMORY: (no relevant past cases or lessons found)"
    lines = ["MEMORY (past cases and lessons from this desk, most relevant first):"]
    for m in memories:
        when = f" [{m.occurred[:10]}]" if m.occurred else ""
        kind = f" ({m.type})" if m.type else ""
        rc = f" root_cause={m.root_cause}" if m.root_cause else ""
        cid = f" case={m.case_id}" if m.case_id else ""
        text = m.text if len(m.text) <= 500 else m.text[:500] + "..."
        lines.append(f"{m.ref}{kind}{when}{cid}{rc}: {text}")
    return "\n".join(lines)


def diagnosis_user_prompt(case: dict[str, Any], memories: list[Any] | None) -> str:
    parts = ["CASE:", case_summary(case), ""]
    if memories is not None:
        parts.append(memory_block(memories))
        parts.append("")
    parts.append("Diagnose this exception. Return the JSON object.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Memory content written by retain()
# ---------------------------------------------------------------------------

def resolved_case_content(case: dict[str, Any], root_cause: str, root_cause_label: str, note: str,
                          agent_root_cause: str | None, agent_confidence: float | None) -> str:
    p = case["payment"]
    bank = p["creditor_bank"]
    inter = f" via intermediary {p['intermediary_bank']['name']} ({p['intermediary_bank']['bic'][:8]})" if p.get("intermediary_bank") else ""
    reason = f" {case['reason_code']} ({case['reason_text']})" if case.get("reason_code") else ""
    text = [
        f"Payment exception {case['case_id']} opened {fmt_time(case['created_at'])}.",
        f"Client {p['debtor']['name']} sent {p['currency']} {p['amount']:,.2f} to {p['creditor']['name']} "
        f"at {bank['name']} ({bank['bic'][:8]}, {bank['city']}, {bank['country']}){inter}. "
        f"Submitted {fmt_time(p['submitted_at'])}. Remittance info: \"{p.get('remittance_info', '')}\".",
        f"Exception: {case['exception_type'].replace('_', ' ').lower()}{reason}. Message: \"{case['counterparty_message']}\"",
    ]
    if case.get("tracker"):
        text.append(case["tracker"] + ".")
    text.append(f"Resolution by the operations analyst: {note}")
    text.append(f"Root cause: {root_cause} ({root_cause_label}).")
    if agent_root_cause:
        conf = f" with {agent_confidence:.0%} confidence" if agent_confidence is not None else ""
        if agent_root_cause == root_cause:
            text.append(f"I (DejaVu) had diagnosed this correctly as {agent_root_cause}{conf}.")
        else:
            text.append(
                f"I (DejaVu) had diagnosed this as {agent_root_cause}{conf}, which was wrong; the correct root cause "
                f"was {root_cause}. Lesson for me: next time at {bank['name']} check for this before assuming "
                f"{agent_root_cause}."
            )
    return "\n".join(text)


# ---------------------------------------------------------------------------
# Pre-flight check (Hindsight reflect with a response schema)
# ---------------------------------------------------------------------------

PRECHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "description": "low, medium or high"},
        "summary": {"type": "string"},
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "severity": {"type": "string", "description": "low, medium or high"},
                    "fix": {"type": "string"},
                },
            },
        },
    },
}


def precheck_query(req: Any) -> str:
    when = f"{req.submit_date + ' ' if req.submit_date else ''}{req.submit_time_sgt} SGT"
    parts = [
        "An analyst is about to send this outgoing payment:",
        f"- Client: {req.client}",
        f"- Beneficiary: {req.beneficiary} at {req.beneficiary_bank}",
        f"- Amount: {req.currency} {req.amount:,.2f}",
        f"- Planned submission time: {when}",
    ]
    if req.intermediary:
        parts.append(f"- Routing via intermediary: {req.intermediary}")
    if req.beneficiary_account:
        digits = _digits(req.beneficiary_account)
        parts.append(f"- Beneficiary account has {len(digits)} digits (ends {digits[-4:]})")
    if req.remittance_info is not None:
        parts.append(f"- Remittance info: \"{req.remittance_info}\"")
    parts.append(
        "Using only this desk's memory of past payment exceptions, will this payment fail or be delayed? "
        "False alarms waste the desk's time. Warn only if memory shows a past failure at this same bank, for this "
        "beneficiary, or for this whole currency corridor (e.g. inward INR needs an RBI purpose code), AND this "
        "payment has the same problem. Ignore issues seen only at other banks, the client's unrelated history and "
        "general good practice. Don't warn about anything the payment already has (an invoice number, a purpose "
        "code such as P0102, the full legal name). risk_level: high if a matching past failure applies; medium if "
        "this bank has a known issue that may or may not apply; low if nothing applies (then no warnings, and say "
        "the desk has no matching history)."
    )
    return "\n".join(parts)


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
