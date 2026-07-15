# pyright: reportUnknownMemberType=false
import streamlit as st

from dashboard_api import get_json

st.title("Audit explorer")
st.caption("Append-only sanitized business audit events; no PDF contents or secrets.")
payload = get_json("/api/v1/operations/audit")
if isinstance(payload, list):
    event_types = sorted({str(row.get("event_type")) for row in payload})
    selected = st.multiselect("Event types", event_types, default=event_types)
    filtered = [row for row in payload if str(row.get("event_type")) in selected]
    st.dataframe(filtered, width="stretch", hide_index=True)
