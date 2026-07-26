# pyright: reportUnknownMemberType=false
from __future__ import annotations

import os
from typing import cast

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("DASHBOARD_API_BASE_URL", "http://api:8000").rstrip("/")
DEMO_DASHBOARD_ROLES = ("OPERATIONS_ADMIN", "AUDITOR")
JsonPayload = dict[str, object] | list[dict[str, object]]


def _demo_role_login_enabled() -> bool:
    return os.environ.get("DASHBOARD_DEMO_ROLE_LOGIN_ENABLED", "false").casefold() == "true"


def _api_client() -> httpx.Client:
    existing = st.session_state.get("_dashboard_api_client")
    if isinstance(existing, httpx.Client):
        return existing
    client = httpx.Client(base_url=API_BASE_URL, timeout=10.0)
    st.session_state["_dashboard_api_client"] = client
    return client


def _request_authentication() -> tuple[httpx.Client, dict[str, str]] | None:
    client = _api_client()
    identity_value = st.session_state.get("_dashboard_demo_identity")
    if isinstance(identity_value, dict):
        identity = cast(dict[str, object], identity_value)
        role = str(identity.get("role", "UNKNOWN")).replace("_", " ").title()
        st.sidebar.success(f"Connected as {role}")
        if st.sidebar.button("Sign out", key="demo_sign_out"):
            try:
                client.delete("/api/v1/demo-auth/session").raise_for_status()
            except httpx.HTTPError:
                st.sidebar.error("The demo session could not be closed.")
            else:
                del st.session_state["_dashboard_demo_identity"]
                st.rerun()
        return client, {}

    if _demo_role_login_enabled():
        st.sidebar.subheader("Local demo session")
        role = st.sidebar.selectbox(
            "Connect as",
            DEMO_DASHBOARD_ROLES,
            format_func=lambda value: value.replace("_", " ").title(),
            key="demo_role",
        )
        if st.sidebar.button("Connect", type="primary", key="demo_connect"):
            try:
                response = client.post("/api/v1/demo-auth/session", json={"role": role})
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("Demo session response was not an object")
            except (httpx.HTTPError, TypeError):
                st.sidebar.error("The local demo session could not be established.")
            else:
                st.session_state["_dashboard_demo_identity"] = payload
                st.rerun()

    token_label = "Bearer token fallback" if _demo_role_login_enabled() else "FastAPI access token"
    token = st.sidebar.text_input(token_label, type="password", key="api_access_token")
    if not token:
        if _demo_role_login_enabled():
            st.info("Connect as an operations admin or auditor in the sidebar.")
        else:
            st.info("Enter an operations-admin or auditor FastAPI token in the sidebar.")
        return None
    return client, {"Authorization": f"Bearer {token}"}


def get_json(path: str) -> JsonPayload | None:
    authentication = _request_authentication()
    if authentication is None:
        return None
    client, headers = authentication
    try:
        response = client.get(path, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            st.error("The FastAPI session is not authenticated. Connect again.")
        elif exc.response.status_code == 403:
            st.error("This role is not authorized for the requested operations projection.")
        else:
            st.error("FastAPI could not provide this operations projection.")
        return None
    except httpx.RequestError:
        st.error("The dashboard could not reach FastAPI.")
        return None
    return cast(JsonPayload, response.json())


def post_json(path: str) -> dict[str, object] | None:
    authentication = _current_authentication()
    if authentication is None:
        st.error("Connect as an operations administrator before running this command.")
        return None
    client, headers = authentication
    try:
        response = client.post(path, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            st.error("Only an operations administrator may run this command.")
        else:
            st.error("The authorized FastAPI command failed.")
        return None
    except httpx.RequestError:
        st.error("The authorized FastAPI command failed.")
        return None
    return cast(dict[str, object], response.json())


def _current_authentication() -> tuple[httpx.Client, dict[str, str]] | None:
    client = _api_client()
    if isinstance(st.session_state.get("_dashboard_demo_identity"), dict):
        return client, {}
    token = st.session_state.get("api_access_token")
    if isinstance(token, str) and token:
        return client, {"Authorization": f"Bearer {token}"}
    return None


def rows_page(title: str, resource: str, description: str) -> None:
    st.title(title)
    st.caption(description)
    payload = get_json(f"/api/v1/operations/{resource}")
    if isinstance(payload, list):
        st.metric("Rows", len(payload))
        st.dataframe(payload, width="stretch", hide_index=True)
        if not payload:
            st.info("No records are available for this tenant.")
