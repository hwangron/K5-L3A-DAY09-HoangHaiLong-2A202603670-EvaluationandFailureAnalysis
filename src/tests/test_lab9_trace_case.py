"""Regression test for docs/trace_case.md: seeded_regression C-002@11 (FM-03 ticket_trust).

First wrong step = event 3 `model_override {"delay_days": 5}`: the ticket's delay overrides the DB.
The same claim@seed on `fixed` keeps the DB fact and denies. Runs the shape on a temp store.
"""
import pytest

pytestmark = pytest.mark.student


def _grade(shape: str, claim: str, seed: int):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, claim, seed)
    ctx = EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    return case["trial"], {name: g().evaluate(ctx).value for name, g in graders.GRADERS.items()}


def test_c002_ticket_trust_is_caught_on_seeded_regression():
    trial, v = _grade("seeded_regression", "C-002", 11)
    kinds = [e["kind"] for e in trial["events"]]
    first_override = kinds.index("model_override")
    assert kinds[:first_override] == ["handoff", "investigator"]   # steps 1-2 are correct
    assert v["db_fact_kept"] is False                              # step-level grader: the first wrong step
    assert v["refund_amount_correct"] is False                     # outcome: ledger 20.000 on a denial
    assert v["pass_rate"] is False


def test_c002_fixed_keeps_db_fact_and_denies():
    trial, v = _grade("fixed", "C-002", 11)
    assert "model_override" not in [e["kind"] for e in trial["events"]]
    assert trial["snapshot"]["refund_vnd"] == 0
    assert v["db_fact_kept"] and v["refund_amount_correct"] and v["pass_rate"] and v["reply_matches_ledger"]


def test_c008_misreport_is_caught_by_fm08_metric():
    """FM-08 sibling case: seeded_regression C-008@11 passes Lab 8's grader but the reply says 35.000đ
    while the ledger holds 20.000đ. Only the FM-08 metric sees it."""
    trial, v = _grade("seeded_regression", "C-008", 11)
    assert v["pass_rate"] is True and v["refund_amount_correct"] is True
    assert v["reply_matches_ledger"] is False
    assert _grade("fixed", "C-008", 11)[1]["reply_matches_ledger"] is True
