"""Supabase OAuth and QCC employee authorization for the Reflex application."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import database_url, query_frame


SESSION_HOURS = 12
EMPLOYEE_ROLES = ("Sales", "Manager", "Admin")


def supabase_project_url() -> str:
    """Return the public Supabase project URL used by the Auth service."""
    return (
        os.getenv("QCC_SUPABASE_URL", "").strip()
        or os.getenv("SUPABASE_URL", "").strip()
    ).rstrip("/")


def supabase_publishable_key() -> str:
    """Return the browser-safe publishable (legacy anon is also supported) key."""
    return (
        os.getenv("QCC_SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.getenv("QCC_SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )


def public_app_url() -> str:
    """Canonical app origin used in Supabase's OAuth redirect allowlist."""
    return (
        os.getenv("QCC_PUBLIC_APP_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        or "http://localhost:3000"
    ).rstrip("/")


def auth_is_configured() -> bool:
    return bool(
        database_url()
        and supabase_project_url()
        and supabase_publishable_key()
        and public_app_url()
    )


def oauth_redirect_url() -> str:
    return f"{public_app_url()}/auth/callback"


def oauth_authorize_url(provider: str) -> str:
    """Build a Supabase social-login URL for Google or Microsoft."""
    normalized = provider.strip().lower()
    if normalized not in {"google", "azure"}:
        raise ValueError("Unsupported authentication provider.")
    if not auth_is_configured():
        raise RuntimeError("Supabase authentication is not configured.")
    parameters = {
        "provider": normalized,
        "redirect_to": oauth_redirect_url(),
    }
    if normalized == "azure":
        parameters["scopes"] = "email"
    return f"{supabase_project_url()}/auth/v1/authorize?{urlencode(parameters)}"


def verify_supabase_access_token(access_token: str) -> dict[str, Any]:
    """Validate a Supabase JWT with the Auth service and return its user."""
    if not access_token:
        raise ValueError("The sign-in response did not include an access token.")
    request = Request(
        f"{supabase_project_url()}/auth/v1/user",
        headers={
            "apikey": supabase_publishable_key(),
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("email"):
        raise ValueError("Supabase did not return a verified email address.")
    return payload


def _ensure_employee_identity_table() -> None:
    """Add a durable many-login-to-one-employee directory beside legacy profiles."""
    import psycopg

    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qcc_employee_login_identities (
                identity_email TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'any',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_qcc_employee_identity_employee "
            "ON qcc_employee_login_identities(employee_id)"
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            """
            INSERT INTO qcc_employee_login_identities
                (identity_email, employee_id, provider, is_active, created_at, updated_at)
            SELECT LOWER(TRIM(user_email)), employee_id, 'legacy', is_active, %s, %s
            FROM sales_user_profiles
            WHERE TRIM(COALESCE(user_email, '')) <> ''
              AND TRIM(COALESCE(employee_id, '')) <> ''
            ON CONFLICT (identity_email) DO NOTHING
            """,
            (now, now),
        )
        connection.commit()


def load_active_employee(email: str) -> dict[str, Any] | None:
    """Match a verified login identity to one active QCC employee."""
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    _ensure_employee_identity_table()
    profiles = query_frame(
        "SELECT p.user_email, p.employee_id, p.full_name, p.title, p.user_role, "
        "p.is_active, i.identity_email AS login_email "
        "FROM qcc_employee_login_identities i "
        "JOIN sales_user_profiles p ON p.employee_id = i.employee_id "
        "WHERE LOWER(i.identity_email) = %s AND i.is_active = 1 LIMIT 1",
        (normalized,),
    )
    if profiles.empty:
        profiles = query_frame(
            "SELECT user_email, employee_id, full_name, title, user_role, "
            "is_active, LOWER(user_email) AS login_email "
            "FROM sales_user_profiles WHERE LOWER(user_email) = %s LIMIT 1",
            (normalized,),
        )
    if profiles.empty:
        return None
    profile = profiles.iloc[0].to_dict()
    if not bool(profile.get("is_active", 0)):
        return None
    return profile


def list_employee_directory() -> list[dict[str, Any]]:
    """Return every employee and all accepted OAuth login emails."""
    _ensure_employee_identity_table()
    profiles = query_frame(
        "SELECT employee_id, full_name, title, user_role, is_active, user_email, "
        "COALESCE(phone, '') AS phone, COALESCE(contact_email, '') AS contact_email "
        "FROM sales_user_profiles ORDER BY is_active DESC, full_name, employee_id"
    )
    identities = query_frame(
        "SELECT employee_id, identity_email, provider, is_active "
        "FROM qcc_employee_login_identities ORDER BY identity_email"
    )
    rows: list[dict[str, Any]] = []
    for profile in profiles.to_dict("records"):
        employee_id = str(profile.get("employee_id", ""))
        employee_identities = identities[
            identities["employee_id"].astype(str).eq(employee_id)
        ] if not identities.empty else identities
        active_emails = employee_identities[
            employee_identities["is_active"].astype(bool)
        ]["identity_email"].astype(str).tolist() if not employee_identities.empty else []
        primary = str(profile.get("user_email", "") or "").strip().lower()
        if primary and primary not in active_emails:
            active_emails.insert(0, primary)
        rows.append({
            "Employee ID": employee_id,
            "Name": str(profile.get("full_name", "")),
            "Title": str(profile.get("title", "")),
            "Role": str(profile.get("user_role", "Sales")),
            "Active": bool(profile.get("is_active", 0)),
            "Primary Email": primary,
            "Login Emails": ", ".join(active_emails),
            "Phone": str(profile.get("phone", "") or ""),
            "Contact Email": str(profile.get("contact_email", "") or ""),
        })
    return rows


def _require_admin(employee_id: str) -> None:
    profiles = query_frame(
        "SELECT user_role, is_active FROM sales_user_profiles "
        "WHERE employee_id = %s LIMIT 1",
        (str(employee_id or ""),),
    )
    if profiles.empty or str(profiles.iloc[0].get("user_role", "")) != "Admin" \
            or not bool(profiles.iloc[0].get("is_active", 0)):
        raise PermissionError("Only an active Administrator can manage Team & Access.")


def create_employee_profile(
    administrator_id: str,
    full_name: str,
    title: str,
    primary_email: str,
    role: str,
    alternate_email: str = "",
) -> str:
    """Create a distinct employee without deriving identity from an email address."""
    import psycopg

    _require_admin(administrator_id)
    name = str(full_name or "").strip()
    job_title = str(title or "").strip()
    primary = str(primary_email or "").strip().lower()
    alternate = str(alternate_email or "").strip().lower()
    normalized_role = str(role or "Sales").strip()
    if not name or not job_title:
        raise ValueError("Full name and title are required.")
    if normalized_role not in EMPLOYEE_ROLES:
        raise ValueError("Role must be Sales, Manager, or Admin.")
    emails = list(dict.fromkeys(email for email in (primary, alternate) if email))
    if not emails or any("@" not in email for email in emails):
        raise ValueError("Enter at least one valid Google or Microsoft login email.")
    _ensure_employee_identity_table()
    employee_id = "QCC-EMP-" + secrets.token_hex(6).upper()
    now = datetime.now(timezone.utc)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        duplicate = connection.execute(
            "SELECT identity_email FROM qcc_employee_login_identities "
            "WHERE LOWER(identity_email) = ANY(%s)",
            (emails,),
        ).fetchone()
        if duplicate:
            raise ValueError(f"A QCC employee already uses {duplicate[0]}.")
        connection.execute(
            "INSERT INTO sales_user_profiles "
            "(user_email, employee_id, full_name, title, phone, contact_email, "
            "user_role, is_active, is_test_account, updated_at) "
            "VALUES (%s, %s, %s, %s, '', '', %s, 1, 0, %s)",
            (primary, employee_id, name, job_title, normalized_role, now.isoformat()),
        )
        for email in emails:
            connection.execute(
                "INSERT INTO qcc_employee_login_identities "
                "(identity_email, employee_id, provider, is_active, created_at, updated_at) "
                "VALUES (%s, %s, 'any', 1, %s, %s)",
                (email, employee_id, now, now),
            )
        connection.commit()
    return employee_id


def update_employee_access(
    administrator_id: str,
    employee_id: str,
    role: str,
    is_active: bool,
    additional_email: str = "",
) -> None:
    """Update role/status and optionally add a non-destructive login identity."""
    import psycopg

    _require_admin(administrator_id)
    normalized_role = str(role or "").strip()
    if normalized_role not in EMPLOYEE_ROLES:
        raise ValueError("Role must be Sales, Manager, or Admin.")
    if employee_id == administrator_id and (not is_active or normalized_role != "Admin"):
        raise ValueError("You cannot remove your own active Administrator access.")
    email = str(additional_email or "").strip().lower()
    if email and "@" not in email:
        raise ValueError("Enter a valid Google or Microsoft login email.")
    _ensure_employee_identity_table()
    now = datetime.now(timezone.utc)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        updated = connection.execute(
            "UPDATE sales_user_profiles SET user_role = %s, is_active = %s, "
            "updated_at = %s WHERE employee_id = %s",
            (normalized_role, int(bool(is_active)), now.isoformat(), employee_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Employee account was not found.")
        connection.execute(
            "UPDATE qcc_employee_login_identities SET is_active = %s, updated_at = %s "
            "WHERE employee_id = %s",
            (int(bool(is_active)), now, employee_id),
        )
        if email:
            existing = connection.execute(
                "SELECT employee_id FROM qcc_employee_login_identities "
                "WHERE LOWER(identity_email) = %s",
                (email,),
            ).fetchone()
            if existing and str(existing[0]) != employee_id:
                raise ValueError("Another employee already uses that login email.")
            connection.execute(
                "INSERT INTO qcc_employee_login_identities "
                "(identity_email, employee_id, provider, is_active, created_at, updated_at) "
                "VALUES (%s, %s, 'any', %s, %s, %s) "
                "ON CONFLICT (identity_email) DO UPDATE SET is_active = EXCLUDED.is_active, "
                "updated_at = EXCLUDED.updated_at",
                (email, employee_id, int(bool(is_active)), now, now),
            )
        connection.commit()


def _ensure_session_table() -> None:
    """Create the small server-side session table without altering employee data."""
    import psycopg

    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qcc_auth_sessions (
                session_hash TEXT PRIMARY KEY,
                user_email TEXT NOT NULL,
                auth_provider TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_qcc_auth_sessions_email "
            "ON qcc_auth_sessions(user_email)"
        )
        connection.commit()


def _session_hash(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def create_app_session(email: str, provider: str) -> str:
    """Issue an opaque QCC session after Supabase and employee checks succeed."""
    import psycopg

    _ensure_session_table()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=SESSION_HOURS)
    session_token = secrets.token_urlsafe(48)
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            "DELETE FROM qcc_auth_sessions WHERE expires_at <= %s OR "
            "revoked_at IS NOT NULL",
            (now,),
        )
        connection.execute(
            "INSERT INTO qcc_auth_sessions (session_hash, user_email, "
            "auth_provider, created_at, expires_at, last_seen_at, revoked_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, NULL)",
            (
                _session_hash(session_token),
                email.strip().lower(),
                provider,
                now,
                expires_at,
                now,
            ),
        )
        connection.commit()
    return session_token


def validate_app_session(session_token: str) -> dict[str, Any] | None:
    """Validate the opaque session and re-check the active employee record."""
    import psycopg

    if not session_token or not database_url():
        return None
    _ensure_session_table()
    now = datetime.now(timezone.utc)
    sessions = query_frame(
        "SELECT user_email, auth_provider, expires_at FROM qcc_auth_sessions "
        "WHERE session_hash = %s AND revoked_at IS NULL AND expires_at > %s "
        "LIMIT 1",
        (_session_hash(session_token), now),
    )
    if sessions.empty:
        return None
    session = sessions.iloc[0].to_dict()
    employee = load_active_employee(str(session.get("user_email", "")))
    if employee is None:
        revoke_app_session(session_token)
        return None
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            "UPDATE qcc_auth_sessions SET last_seen_at = %s WHERE session_hash = %s",
            (now, _session_hash(session_token)),
        )
        connection.commit()
    employee["auth_provider"] = str(session.get("auth_provider", ""))
    return employee


def revoke_app_session(session_token: str) -> None:
    if not session_token or not database_url():
        return
    import psycopg

    _ensure_session_table()
    with psycopg.connect(database_url(), connect_timeout=15) as connection:
        connection.execute(
            "UPDATE qcc_auth_sessions SET revoked_at = %s WHERE session_hash = %s",
            (datetime.now(timezone.utc), _session_hash(session_token)),
        )
        connection.commit()
