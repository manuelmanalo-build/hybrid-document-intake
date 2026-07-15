# pyright: reportUnknownMemberType=false
from decimal import Decimal

import streamlit as st

from dashboard_api import get_json

st.title("Overview")
st.caption("Current tenant throughput, quality, review, and delivery health from FastAPI.")
payload = get_json("/api/v1/operations/overview")
if isinstance(payload, dict):
    first = st.columns(5)
    first[0].metric("Uploads", str(payload.get("uploads", 0)))
    first[1].metric("Graph completed", str(payload.get("graph_completion", 0)))
    first[2].metric("Interrupted", str(payload.get("interrupted_graph_count", 0)))
    first[3].metric("Agent completed", str(payload.get("agent_completion", 0)))
    first[4].metric("Tool calls", str(payload.get("agent_tool_calls", 0)))
    second = st.columns(5)
    second[0].metric("Extraction attempts", str(payload.get("extraction_attempts", 0)))
    second[1].metric("Low-confidence fields", str(payload.get("low_confidence_fields", 0)))
    second[2].metric("Rule failures", str(payload.get("rule_failures", 0)))
    second[3].metric("Corrections", str(payload.get("human_corrections", 0)))
    second[4].metric("Approvals", str(payload.get("approvals", 0)))
    third = st.columns(5)
    third[0].metric("Webhook success", str(payload.get("webhook_success", 0)))
    third[1].metric("Webhook retries", str(payload.get("webhook_retries", 0)))
    third[2].metric("Dead letters", str(payload.get("dead_letter_count", 0)))
    third[3].metric("Token usage", str(payload.get("token_usage", 0)))
    confidence = payload.get("average_confidence")
    third[4].metric(
        "Average confidence",
        f"{Decimal(str(confidence)):.2%}" if confidence is not None else "—",
    )
    fourth = st.columns(4)
    fourth[0].metric(
        "Human correction rate",
        f"{Decimal(str(payload.get('human_correction_rate', 0))):.2%}",
    )
    fourth[1].metric("Approval rate", f"{Decimal(str(payload.get('approval_rate', 0))):.2%}")
    review_duration = payload.get("average_review_duration_seconds")
    fourth[2].metric(
        "Average review duration",
        f"{Decimal(str(review_duration)):.0f}s" if review_duration is not None else "—",
    )
    fourth[3].metric("Completed reviews", str(payload.get("completed_reviews", 0)))
    st.subheader("Confidence distribution")
    st.bar_chart(payload.get("confidence_distribution", {}))
    st.subheader("Rule-failure frequency")
    st.bar_chart(payload.get("rule_failure_frequency", {}))
