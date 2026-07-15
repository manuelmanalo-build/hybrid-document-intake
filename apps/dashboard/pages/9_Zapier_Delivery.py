# pyright: reportUnknownMemberType=false
import streamlit as st

from dashboard_api import get_json, post_json

st.title("Zapier delivery")
st.caption("Delivery ledger and operations-admin dead-letter replay through FastAPI commands.")
payload = get_json("/api/v1/operations/zapier-delivery")
if isinstance(payload, list):
    st.dataframe(payload, width="stretch", hide_index=True)
    dead_letters = [row for row in payload if row.get("status") == "DEAD_LETTER"]
    if dead_letters:
        selected = st.selectbox("Dead-letter event", [str(row["event_id"]) for row in dead_letters])
        if st.button("Replay selected event", type="primary"):
            result = post_json(f"/api/v1/integrations/outbox/{selected}/replay")
            if result:
                st.success("Replay accepted by FastAPI.")
    else:
        st.success("No dead-letter events are present.")
