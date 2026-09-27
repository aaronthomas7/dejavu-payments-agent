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


def test_known_false_positive_still_needs_a_human():
    d = guardrails.apply({"exception_type": "SCREENING_HOLD"}, _diag(root_cause="SANCTIONS_KNOWN_FALSE_POSITIVE"), True)
    assert d["root_cause"] == "SANCTIONS_KNOWN_FALSE_POSITIVE"
    assert d["requires_human_approval"] and not d["auto_fix_eligible"]


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
