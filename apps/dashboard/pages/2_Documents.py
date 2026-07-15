# pyright: reportUnknownMemberType=false
import streamlit as st

from dashboard_api import get_json

st.title("Documents")
st.caption(
    "Tenant-scoped document processing stages. Open document content in the Angular review "
    "application."
)
payload = get_json("/api/v1/documents")
if isinstance(payload, list):
    st.metric("Documents", len(payload))
    st.dataframe(payload, width="stretch", hide_index=True)
