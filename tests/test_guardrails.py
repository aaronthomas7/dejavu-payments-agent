from app import guardrails
from app.taxonomy import UNKNOWN, normalize_code


def _diag(**kw):
    base = {"root_cause": "CLIENT_TYPO_IN_DETAILS", "confidence": 0.9, "reasoning": "", "evidence": [{"memory_ref": "M1"}],
            "recommended_action": "Fix it", "draft_message": "", "prevention_tip": ""}
    base.update(kw)
    return base


def test_screening_hold_is_always_a_human_sanctions_decision():
    case = {"exception_type": "SCREENING_HOLD"}
    d = guardrails.apply(case, _diag(root_cause="CLIENT_TYPO_IN_DETAILS"), used_memory=True)
    assert d["root_cause"] == "SANCTIONS_POTENTIAL_MATCH"
    assert d["requires_human_approval"] is True
    assert d["auto_fix_eligible"] is False
    assert "compliance" in d["recommended_action"].lower()


def _hold(beneficiary):
    return {"exception_type": "SCREENING_HOLD", "case_id": "EXC-1",
            "counterparty_message": "Sanctions screening hit. Payment held.",
            "payment": {"payment_ref": "PAY-1234567", "amount": 13110.0, "currency": "USD",
                        "debtor": {"name": "Blue Harbour Logistics Pte Ltd"}, "creditor": {"name": beneficiary}}}


DESERT_STAR_CLEARED = [{"memory_ref": "M1", "text": "EXC-260819-07: payment to Desert Star General Trading LLC held "
                        "(match vs Desert Star Shipping FZE). Compliance cleared it as a false positive."}]


def test_known_false_positive_still_needs_a_human():
    d = guardrails.apply(_hold("DESERT STAR GENERAL TRADING LLC"),
                         _diag(root_cause="SANCTIONS_KNOWN_FALSE_POSITIVE", evidence=DESERT_STAR_CLEARED), True)
    assert d["root_cause"] == "SANCTIONS_KNOWN_FALSE_POSITIVE"
    assert d["confidence"] == 0.9 and d["evidence"] == DESERT_STAR_CLEARED
    assert d["requires_human_approval"] and not d["auto_fix_eligible"]


def test_a_lookalike_name_is_not_a_known_false_positive():
    # Clearances for Desert Star must not carry over to Red Sea Star, however similar the alert looks.
    d = guardrails.apply(_hold("Red Sea Star Logistics LLC"),
                         _diag(root_cause="SANCTIONS_KNOWN_FALSE_POSITIVE", evidence=DESERT_STAR_CLEARED), True)
    assert d["root_cause"] == "SANCTIONS_POTENTIAL_MATCH"
    assert d["confidence"] <= guardrails.NO_EVIDENCE_MAX_CONFIDENCE and d["evidence"] == []
    assert d["requires_human_approval"] and not d["auto_fix_eligible"]
    assert "Red Sea Star Logistics LLC" in d["draft_message"] and "compliance" in d["recommended_action"].lower()
    assert any("No past clearance" in n for n in d["guardrail_notes"])
    # and a known false positive with no evidence at all is not accepted either
    d2 = guardrails.apply(_hold("Desert Star General Trading LLC"),
                          _diag(root_cause="SANCTIONS_KNOWN_FALSE_POSITIVE", evidence=[]), True)
    assert d2["root_cause"] == "SANCTIONS_POTENTIAL_MATCH"


def test_the_listed_entity_itself_is_never_a_known_false_positive():
    # Past alerts mention the list entry's name, so the evidence check alone would pass here.
    case = _hold("DESERT STAR SHIPPING FZE")
    case["counterparty_message"] = ("Sanctions screening hit: beneficiary 'DESERT STAR SHIPPING FZE' vs list entry "
                                    "'DESERT STAR SHIPPING FZE' (match score 1.00). Payment held.")
    d = guardrails.apply(case, _diag(root_cause="SANCTIONS_KNOWN_FALSE_POSITIVE", evidence=DESERT_STAR_CLEARED
                                     + [{"memory_ref": "M2", "text": "Alert vs Desert Star Shipping FZE cleared."}]), True)
    assert d["root_cause"] == "SANCTIONS_POTENTIAL_MATCH"
    assert any("watch-list entry" in n for n in d["guardrail_notes"])
    assert not guardrails.names_listed_entity(_hold("Desert Star General Trading LLC") | {
        "counterparty_message": "beneficiary matched watch-list entry 'Desert Star Shipping FZE' (score 0.86)"})


def test_no_evidence_caps_confidence():
    d = guardrails.apply({"exception_type": "REJECTED"}, _diag(evidence=[], confidence=0.95), used_memory=True)
    assert d["confidence"] == guardrails.NO_EVIDENCE_MAX_CONFIDENCE
    assert not d["auto_fix_eligible"]
    d2 = guardrails.apply({"exception_type": "REJECTED"}, _diag(confidence=0.95), used_memory=False)
    assert d2["confidence"] == guardrails.NO_EVIDENCE_MAX_CONFIDENCE


def test_confident_evidenced_diagnosis_is_one_click():
    d = guardrails.apply({"exception_type": "REJECTED"}, _diag(confidence=0.9), used_memory=True)
    assert d["auto_fix_eligible"] and not d["requires_human_approval"]


def test_account_numbers_are_masked_but_references_are_not():
    text = "Resend to 601620144880913 and IBAN DE89370400440532013000; invoice KCS-55812, case EXC-260826-01, PAY-7637059"
    masked = guardrails.mask_accounts(text)
    assert "601620144880913" not in masked and "0913" in masked
    assert "DE89370400440532013000" not in masked and "3000" in masked
    assert "KCS-55812" in masked and "EXC-260826-01" in masked and "PAY-7637059" in masked


def test_normalize_code_accepts_sloppy_labels():
    assert normalize_code("account_format_changed") == "ACCOUNT_FORMAT_CHANGED"
    assert normalize_code("Known sanctions false positive") == "SANCTIONS_KNOWN_FALSE_POSITIVE"
    assert normalize_code("ACCOUNT_FORMAT") == "ACCOUNT_FORMAT_CHANGED"
    assert normalize_code("something else") == UNKNOWN
    assert normalize_code(None) == UNKNOWN
