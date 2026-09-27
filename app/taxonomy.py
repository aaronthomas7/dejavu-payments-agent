"""Root-cause taxonomy for payment exceptions.

Every diagnosis DejaVu makes picks exactly one root cause from this list. Keeping
the label set closed is what lets us *measure* whether the agent is learning:
each synthetic case carries a ground-truth root cause, so accuracy is a number,
not a vibe.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RootCause:
    code: str
    label: str
    description: str
    default_fix: str
    human_only: bool = False  # sanctions-type causes can never be auto-actioned


ROOT_CAUSES: list[RootCause] = [
    RootCause(
        "ACCOUNT_FORMAT_CHANGED",
        "Beneficiary bank changed account format",
        "The beneficiary bank migrated to a new account-number format; old-format numbers are rejected.",
        "Convert the account number to the bank's new format and resend.",
    ),
    RootCause(
        "CLIENT_TYPO_IN_DETAILS",
        "Client keyed wrong details",
        "One-off data-entry error by the client (account number, IBAN, BIC or name typo).",
        "Ask the client for the correct details, repair and resend.",
    ),
    RootCause(
        "MISSED_CUTOFF_NEXT_DAY_CREDIT",
        "Arrived after bank cut-off",
        "The payment reached the beneficiary bank after its same-day cut-off, so it credits the next business day.",
        "Confirm next-day credit to the client; no trace needed. Advise earlier submission.",
    ),
    RootCause(
        "INTERMEDIARY_DELAY",
        "Held at an intermediary bank",
        "Funds are held or delayed at an intermediary/correspondent bank.",
        "Send a trace (MT199 / camt.027) to the intermediary and follow up.",
    ),
    RootCause(
        "NAME_LEGAL_SUFFIX_MISMATCH",
        "Name must match registered legal name",
        "The beneficiary bank does exact name matching; abbreviated company suffixes (Pvt Ltd) are rejected.",
        "Amend the beneficiary name to the full registered legal name and resend.",
    ),
    RootCause(
        "SANCTIONS_KNOWN_FALSE_POSITIVE",
        "Known sanctions false positive",
        "Screening hit on a beneficiary that Compliance has previously cleared as a false positive.",
        "Route to Compliance with the prior clearance reference. Never auto-release.",
        human_only=True,
    ),
    RootCause(
        "SANCTIONS_POTENTIAL_MATCH",
        "Potential sanctions match",
        "New screening hit that has not been cleared before; needs full Compliance review.",
        "Escalate to Compliance for full review. Never auto-release.",
        human_only=True,
    ),
    RootCause(
        "MISSING_INVOICE_REFERENCE",
        "Invoice number required",
        "The beneficiary bank requires an invoice number in the remittance information for trade payments.",
        "Get the invoice number from the client and resubmit with it in the remittance info.",
    ),
    RootCause(
        "PURPOSE_CODE_MISSING",
        "Regulatory purpose code missing",
        "A regulatory purpose code or reporting field is required for this corridor and was missing.",
        "Get the purpose code from the client and resubmit.",
    ),
    RootCause(
        "CORRESPONDENT_ROUTING_CHANGED",
        "Correspondent / routing changed",
        "The beneficiary bank changed its correspondent bank; the payment went via the old route.",
        "Update standard settlement instructions and resend via the new correspondent.",
    ),
    RootCause(
        "CLIENT_ERP_DUPLICATE",
        "Client system re-sent a file",
        "A duplicate caused by the client's ERP/system re-sending a payment file (known recurring behaviour).",
        "Cancel the duplicate instruction and notify the client's IT team.",
    ),
    RootCause(
        "POSSIBLE_DUPLICATE_VERIFY",
        "Possible duplicate - verify",
        "Looks like a duplicate but may be intentional (e.g. two invoices with the same amount).",
        "Verify with the client before releasing or cancelling.",
    ),
    RootCause(
        "NOSTRO_FUNDING_SHORTFALL",
        "Our nostro account is short",
        "Our own nostro account in that currency lacks funds (e.g. heavy month-end outflows).",
        "Ask Treasury to top up the nostro, then release the payment.",
    ),
    RootCause(
        "CLIENT_INSUFFICIENT_FUNDS",
        "Client account lacks funds",
        "The client's own debit account does not have enough balance.",
        "Hold the payment and ask the client to fund the account.",
    ),
    RootCause(
        "BENEFICIARY_ACCOUNT_CLOSED",
        "Beneficiary account closed",
        "The beneficiary's account has been closed.",
        "Return funds and ask the client for the beneficiary's new account.",
    ),
]

BY_CODE: dict[str, RootCause] = {rc.code: rc for rc in ROOT_CAUSES}
CODES: list[str] = [rc.code for rc in ROOT_CAUSES]
HUMAN_ONLY_CODES: set[str] = {rc.code for rc in ROOT_CAUSES if rc.human_only}

UNKNOWN = "NEEDS_HUMAN_REVIEW"


def normalize_code(value: str | None) -> str:
    """Map a model-produced label onto the closed taxonomy.

    Models sometimes return lower-case, spaced or slightly different labels. We
    accept those, but anything we cannot map becomes NEEDS_HUMAN_REVIEW rather
    than silently guessing.
    """
    if not value:
        return UNKNOWN
    cleaned = value.strip().upper().replace("-", "_").replace(" ", "_")
    if cleaned in BY_CODE:
        return cleaned
    # tolerate the label text instead of the code
    for rc in ROOT_CAUSES:
        if value.strip().lower() == rc.label.lower():
            return rc.code
    # tolerate a unique prefix, e.g. "ACCOUNT_FORMAT"
    matches = [code for code in CODES if code.startswith(cleaned)]
    if len(matches) == 1:
        return matches[0]
    return UNKNOWN


def taxonomy_for_prompt() -> str:
    return "\n".join(f"- {rc.code}: {rc.description}" for rc in ROOT_CAUSES)
