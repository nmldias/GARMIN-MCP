"""Garmin Connect MCP server (HTTP transport, ready for Render)."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import tarfile
import tempfile
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
from starlette.requests import Request
from starlette.responses import PlainTextResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("garmin-mcp")

# Resolves to /tmp on Linux (Render) and the user temp dir on Windows.
TOKENSTORE = os.environ.get(
    "GARMINTOKENS", os.path.join(tempfile.gettempdir(), ".garminconnect")
)


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

        _bootstrap_tokenstore_from_env()
        have_tokens = os.path.isdir(TOKENSTORE) and bool(os.listdir(TOKENSTORE))

        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not have_tokens and (not email or not password):
            raise RuntimeError(
                "No Garmin tokenstore found. Set GARMINTOKENS_BASE64, "
                "or set both GARMIN_EMAIL and GARMIN_PASSWORD."
            )

        def _no_mfa() -> str:
            raise RuntimeError(
                "Garmin is requesting MFA but the server is non-interactive. "
                "Run bootstrap_tokens.py locally to populate GARMINTOKENS_BASE64."
            )

        client = Garmin(email=email or "", password=password or "", prompt_mfa=_no_mfa)
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


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    out: list[str] = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


async def _fetch_range(fn, days: list[str], key: str) -> list[dict[str, Any]]:
    results = await asyncio.gather(
        *(asyncio.to_thread(fn, d) for d in days),
        return_exceptions=True,
    )
    out: list[dict[str, Any]] = []
    for d, r in zip(days, results):
        if isinstance(r, Exception):
            out.append({"date": d, "error": str(r)})
        else:
            out.append({"date": d, key: r})
    return out


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


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Unauthenticated liveness probe — the /mcp endpoint requires a token."""
    return PlainTextResponse("ok")


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
async def get_sleep_data_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Nightly sleep analysis for each day in a date range, fetched in parallel."""
    return await _fetch_range(
        garmin().get_sleep_data, _date_range(start_date, end_date), "sleep"
    )


@mcp.tool
async def get_hrv_data_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Overnight HRV for each day in a date range, fetched in parallel."""
    return await _fetch_range(
        garmin().get_hrv_data, _date_range(start_date, end_date), "hrv"
    )


@mcp.tool
async def get_stress_data_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Stress levels and durations for each day in a date range, fetched in parallel."""
    return await _fetch_range(
        garmin().get_stress_data, _date_range(start_date, end_date), "stress"
    )


@mcp.tool
async def get_heart_rates_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Heart-rate samples + resting HR for each day in a date range, fetched in parallel."""
    return await _fetch_range(
        garmin().get_heart_rates, _date_range(start_date, end_date), "heart_rates"
    )


@mcp.tool
async def get_training_readiness_range(
    start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Training readiness scores for each day in a date range, fetched in parallel."""
    return await _fetch_range(
        garmin().get_training_readiness,
        _date_range(start_date, end_date),
        "training_readiness",
    )


@mcp.tool
def get_body_battery_range(start_date: str, end_date: str) -> Any:
    """Body battery values across a date range (single Garmin API call)."""
    return garmin().get_body_battery(start_date, end_date)


@mcp.tool
async def get_last_n_days_summary(days: int = 7) -> list[dict[str, Any]]:
    """Daily stats for the last N days (default 7), fetched in parallel. Handy for trend analysis."""
    c = garmin()
    today = date.today()
    iso_days = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    return await _fetch_range(c.get_stats, iso_days, "stats")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    path = os.environ.get("MCP_PATH", "/mcp")
    log.info("Starting Garmin MCP on http://%s:%s%s", host, port, path)
    mcp.run(transport="http", host=host, port=port, path=path)
