# pyright: reportUnknownMemberType=false
from __future__ import annotations

import os
from typing import cast

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("DASHBOARD_API_BASE_URL", "http://api:8000").rstrip("/")


def access_token() -> str:
    return st.sidebar.text_input("FastAPI access token", type="password", key="api_access_token")


def get_json(path: str) -> dict[str, object] | list[dict[str, object]] | None:
    token = access_token()
    if not token:
        st.info("Enter an operations-admin or auditor FastAPI token in the sidebar.")
        return None
    try:
        response = httpx.get(
            f"{API_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        st.error("FastAPI could not provide this operations projection.")
        return None
    return cast(dict[str, object] | list[dict[str, object]], response.json())


def post_json(path: str) -> dict[str, object] | None:
    token = access_token()
    if not token:
        st.error("An operations-admin FastAPI token is required.")
        return None
    try:
        response = httpx.post(
            f"{API_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        st.error("The authorized FastAPI command failed.")
        return None
    return cast(dict[str, object], response.json())


def rows_page(title: str, resource: str, description: str) -> None:
    st.title(title)
    st.caption(description)
    payload = get_json(f"/api/v1/operations/{resource}")
    if isinstance(payload, list):
        st.metric("Rows", len(payload))
        st.dataframe(payload, width="stretch", hide_index=True)
        if not payload:
            st.info("No records are available for this tenant.")
