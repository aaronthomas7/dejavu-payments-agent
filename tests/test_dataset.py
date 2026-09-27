from collections import Counter

from app import prompts
from app.taxonomy import CODES


def test_every_case_has_a_valid_label(history, live):
    for case in history + live:
        assert case["ground_truth"]["root_cause"] in CODES, case["case_id"]


def test_history_is_chronological_and_unique(history, live):
    times = [c["created_at"] for c in history]
    assert times == sorted(times)
    ids = [c["case_id"] for c in history + live]
    assert len(ids) == len(set(ids))


def test_every_planted_pattern_repeats(history):
    counts = Counter(c["ground_truth"]["pattern_id"] for c in history if c["ground_truth"]["pattern_id"])
    assert set(counts) == {f"P{i}" for i in range(1, 9)}
    assert all(n >= 2 for n in counts.values())


def test_repeat_flag_marks_only_later_occurrences(history):
    seen = set()
    for c in history:
        pid = c["ground_truth"]["pattern_id"]
        assert c["ground_truth"]["is_repeat"] == bool(pid and pid in seen)
        if pid:
            seen.add(pid)


def test_prompt_never_leaks_ground_truth(history, live):
    for case in history + live:
        text = prompts.case_summary(case) + prompts.recall_query(case)
        assert case["ground_truth"]["resolution_note"] not in text
        assert case["ground_truth"]["root_cause"] not in text


def test_noise_shares_error_codes_with_patterns(history):
    # the same error code must map to different root causes, otherwise memory would not matter
    by_code = {}
    for c in history:
        by_code.setdefault(c.get("reason_code") or c["exception_type"], set()).add(c["ground_truth"]["root_cause"])
    assert len(by_code["AC01"]) >= 2
    assert len(by_code["NON_RECEIPT_CLAIM"]) >= 2
    assert len(by_code["SCREENING_HOLD"]) >= 2
