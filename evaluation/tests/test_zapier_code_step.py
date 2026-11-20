from pathlib import Path

CODE = Path("docs/integrations/zapier/jira_code_step.py").read_text()
SAMPLES = Path("docs/integrations/samples")
TOKEN = "a" * 48


def _run(sample: str, *, auth: str = f"Bearer {TOKEN}", expected_type: str) -> dict[str, object]:
    scope: dict[str, object] = {
        "input_data": {
            "body": (SAMPLES / sample).read_text(),
            "auth": auth,
            "token": TOKEN,
            "expected_type": expected_type,
        }
    }
    exec(compile(CODE, "jira_code_step.py", "exec"), scope)  # noqa: S102 - our own versioned file
    output = scope["output"]
    assert isinstance(output, dict)
    return output  # type: ignore[return-value]


def test_brief_sample_flattens_to_three_loop_items() -> None:
    output = _run("approved-brief.json", expected_type="CAMPAIGN_BRIEF")

    assert output["ok"] == "true"
    assert output["has_epic"] == "true"
    assert output["epic_summary"] == "Winter Warmers (Harbor & Vine)"
    assert output["issue_count"] == 3
    assert str(output["issue_due_dates"]).split("~~~") == ["2026-11-20", "2026-11-20", "2026-11-24"]
    assert str(output["event_label"]).startswith("intake-")


def test_cdp_and_claim_samples_reuse_the_same_step() -> None:
    cdp = _run("approved-cdp-onboarding.json", expected_type="CDP_ONBOARDING")
    claim = _run("approved-claim.json", expected_type="CLAIM_PACKET")

    assert cdp["issue_count"] == 5
    assert "Story" in str(cdp["issue_types"])
    assert claim["has_epic"] == "false"
    assert claim["issue_count"] == 1
    assert str(claim["event_label"]).startswith("intake-")


def test_wrong_token_or_type_is_rejected() -> None:
    assert (
        _run("approved-brief.json", auth="Bearer nope", expected_type="CAMPAIGN_BRIEF")["reason"]
        == "Missing or wrong token"
    )
    assert _run("approved-brief.json", expected_type="CDP_ONBOARDING")["reason"] == (
        "Not an approved CDP_ONBOARDING"
    )
