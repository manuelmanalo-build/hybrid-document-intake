import streamlit as st

st.set_page_config(page_title="Document Intake Operations", page_icon="📄", layout="wide")
st.title("Document Intake Operations")
st.caption("API-only operational visibility for the deterministic LangGraph document workflow.")
st.info(
    "Use the pages in the sidebar to inspect processing, extraction, validation, review, audit, "
    "and Zapier delivery. Credentials and database access remain server-side."
)

pages = (
    ("pages/1_Overview.py", "Overview", "📊"),
    ("pages/2_Documents.py", "Documents", "📄"),
    ("pages/3_Graph_Runs.py", "Graph runs", "🔀"),
    ("pages/4_Agent_Runs.py", "Agent runs", "🤖"),
    ("pages/5_Tool_Activity.py", "Tool activity", "🧰"),
    ("pages/6_Extraction_Quality.py", "Extraction quality", "🔎"),
    ("pages/7_Validation_Failures.py", "Validation failures", "⚖️"),
    ("pages/8_Human_Review.py", "Human review", "👤"),
    ("pages/9_Zapier_Delivery.py", "Zapier delivery", "📨"),
    ("pages/10_Audit_Explorer.py", "Audit explorer", "🧾"),
)
for path, label, icon in pages:
    st.page_link(path, label=label, icon=icon)
