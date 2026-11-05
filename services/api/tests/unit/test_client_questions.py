from intake_api.application.client_questions import draft_client_questions

B03 = {
    "campaign_name": "Holiday Smart Gifting",
    "approver": "Jonah Pratt, Marketing Manager",
    "primary_kpi": "Awareness",
    "kpi_target": "Drive awareness of Lumen as a gift",
    "objective": "Position Lumen smart bulbs as the easy holiday gift.",
    "audience": "Gift shoppers 25-54",
    "launch_date": "2026-12-03",
    "deliverables": [{"asset_type": "SOCIAL_STATIC", "quantity": 8}],
}


def test_unmeasurable_kpi_becomes_a_specific_question() -> None:
    draft = draft_client_questions(B03, ["KPI_NOT_MEASURABLE"])

    assert draft.subject == "Questions on the Holiday Smart Gifting brief"
    assert draft.reason_codes == ["KPI_NOT_MEASURABLE"]
    assert len(draft.questions) == 1
    assert '"Drive awareness of Lumen as a gift"' in draft.questions[0]
    assert draft.body.startswith("Hi Jonah,")
    assert "1. What numeric target" in draft.body


def test_missing_budget_and_fields_are_asked_for_and_other_flags_ignored() -> None:
    data = {**B03, "audience": None, "deliverables": []}

    draft = draft_client_questions(
        data, ["BRIEF_INCOMPLETE", "BUDGET_MISSING", "REGULATED_CONTENT", "OVER_SOW_BUDGET"]
    )

    assert draft.reason_codes == ["BRIEF_INCOMPLETE", "BUDGET_MISSING"]
    assert "the target audience and the deliverables" in draft.questions[0]
    assert "approved budget" in draft.questions[1]


def test_a_complete_brief_has_no_draft() -> None:
    draft = draft_client_questions(B03, ["TIMELINE_AT_RISK"])

    assert draft.questions == []
    assert draft.body == ""
