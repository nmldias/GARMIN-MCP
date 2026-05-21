"""Garmin Connect MCP server (HTTP transport, ready for Render)."""

from __future__ import annotations

import base64
import io
import logging
import os
import tarfile
import threading
from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("garmin-mcp")

TOKENSTORE = os.environ.get("GARMINTOKENS", "/tmp/.garminconnect")


def _bootstrap_tokenstore_from_env() -> None:
    """If GARMINTOKENS_BASE64 is set, extract it into TOKENSTORE.

    Tokens are base64-encoded tar.gz of a local ~/.garminconnect directory,
    produced once on a machine that has cleared MFA. This lets the Render
    instance authenticate without needing interactive MFA.
    """
    blob = os.environ.get("GARMINTOKENS_BASE64")
    if not blob or os.path.isdir(TOKENSTORE) and os.listdir(TOKENSTORE):
        return
    os.makedirs(TOKENSTORE, exist_ok=True)
    try:
        raw = base64.b64decode(blob)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            tar.extractall(TOKENSTORE, filter="data")
        log.info("Loaded Garmin tokens from GARMINTOKENS_BASE64 into %s", TOKENSTORE)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to extract GARMINTOKENS_BASE64: %s", exc)


_client: Garmin | None = None
_client_lock = threading.Lock()


def garmin() -> Garmin:
    """Return a logged-in Garmin client, reusing the cached session."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client

        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "GARMIN_EMAIL and GARMIN_PASSWORD must be set in the environment."
            )

        _bootstrap_tokenstore_from_env()

        def _no_mfa() -> str:
            raise RuntimeError(
                "Garmin is requesting MFA but the server is non-interactive. "
                "Run the bootstrap script locally to populate GARMINTOKENS_BASE64."
            )

        client = Garmin(email=email, password=password, prompt_mfa=_no_mfa)
        try:
            client.login(TOKENSTORE)
        except GarminConnectAuthenticationError as exc:
            raise RuntimeError(f"Garmin authentication failed: {exc}") from exc
        except GarminConnectTooManyRequestsError as exc:
            raise RuntimeError(f"Garmin rate-limited the login: {exc}") from exc
        except GarminConnectConnectionError as exc:
            raise RuntimeError(f"Garmin connection error: {exc}") from exc

        _client = client
        return _client


def _iso(day: str | None) -> str:
    return day or date.today().isoformat()


auth_token = os.environ.get("MCP_BEARER_TOKEN")
if auth_token:
    auth = StaticTokenVerifier(
        tokens={auth_token: {"sub": "garmin-mcp", "client_id": "garmin-mcp"}}
    )
    mcp = FastMCP("Garmin MCP", auth=auth)
    log.info("Bearer auth enabled")
else:
    mcp = FastMCP("Garmin MCP")
    log.warning("MCP_BEARER_TOKEN not set — server is UNAUTHENTICATED")


@mcp.tool
def get_user_profile() -> dict[str, Any]:
    """Return the Garmin account display name, full name, and unit system."""
    c = garmin()
    return {
        "display_name": c.display_name,
        "full_name": c.full_name,
        "unit_system": c.unit_system,
    }


@mcp.tool
def get_daily_stats(day: str | None = None) -> dict[str, Any]:
    """Daily summary: steps, calories, resting HR, stress, body battery, intensity minutes.

    Args:
        day: ISO date (YYYY-MM-DD). Defaults to today.
    """
    return garmin().get_stats(_iso(day))


@mcp.tool
def get_steps_intraday(day: str | None = None) -> list[dict[str, Any]]:
    """Intraday step samples for a single day."""
    return garmin().get_steps_data(_iso(day))


@mcp.tool
def get_daily_steps(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Daily step aggregates over a date range. Auto-chunked for ranges > 28 days."""
    return garmin().get_daily_steps(start_date, end_date)


@mcp.tool
def get_heart_rates(day: str | None = None) -> dict[str, Any]:
    """Heart-rate samples plus resting HR for a day."""
    return garmin().get_heart_rates(_iso(day))


@mcp.tool
def get_sleep_data(day: str | None = None) -> dict[str, Any]:
    """Nightly sleep analysis (stages, scores, SpO2, HRV, respiration)."""
    return garmin().get_sleep_data(_iso(day))


@mcp.tool
def get_stress_data(day: str | None = None) -> dict[str, Any]:
    """Stress levels and rest/active durations for a day."""
    return garmin().get_stress_data(_iso(day))


@mcp.tool
def get_body_battery(day: str | None = None) -> Any:
    """Body battery values for a day."""
    d = _iso(day)
    return garmin().get_body_battery(d, d)


@mcp.tool
def get_body_composition(day: str | None = None) -> dict[str, Any]:
    """Weight, BMI, body fat, muscle mass, etc. for a day."""
    return garmin().get_body_composition(_iso(day))


@mcp.tool
def get_floors(day: str | None = None) -> dict[str, Any]:
    """Floors ascended/descended for a day."""
    return garmin().get_floors(_iso(day))


@mcp.tool
def get_hrv_data(day: str | None = None) -> dict[str, Any]:
    """Overnight HRV data for a day."""
    return garmin().get_hrv_data(_iso(day))


@mcp.tool
def get_training_readiness(day: str | None = None) -> list[dict[str, Any]]:
    """Training readiness score(s) for a day."""
    return garmin().get_training_readiness(_iso(day))


@mcp.tool
def get_recent_activities(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent activities (defaults to last 10)."""
    return garmin().get_activities(0, limit)


@mcp.tool
def get_activities_by_date(
    start_date: str,
    end_date: str,
    activity_type: str | None = None,
) -> list[dict[str, Any]]:
    """Activities in a date range, optionally filtered by activity type.

    Args:
        start_date: ISO date YYYY-MM-DD.
        end_date: ISO date YYYY-MM-DD.
        activity_type: e.g. "running", "cycling", "swimming". Optional.
    """
    return garmin().get_activities_by_date(start_date, end_date, activity_type)


@mcp.tool
def get_last_n_days_summary(days: int = 7) -> list[dict[str, Any]]:
    """Daily stats for the last N days (default 7). Handy for trend analysis."""
    c = garmin()
    today = date.today()
    out: list[dict[str, Any]] = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        out.append({"date": d, "stats": c.get_stats(d)})
    return out


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    path = os.environ.get("MCP_PATH", "/mcp")
    log.info("Starting Garmin MCP on http://%s:%s%s", host, port, path)
    mcp.run(transport="http", host=host, port=port, path=path)
