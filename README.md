# Garmin MCP

A Model Context Protocol server that exposes your Garmin Connect data
(steps, sleep, HR, HRV, stress, body battery, activities, training
readiness, body composition) to MCP-compatible clients like Claude.

Built with [FastMCP](https://gofastmcp.com) and
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect).
Runs over HTTP with bearer-token auth — ready to deploy to Render.

## Tools

| Tool | What it returns |
| --- | --- |
| `get_user_profile` | Display name, full name, unit system |
| `get_daily_stats` | Steps, calories, resting HR, stress, body battery, intensity minutes |
| `get_steps_intraday` | Per-bucket step samples for one day |
| `get_daily_steps` | Daily totals across a date range |
| `get_heart_rates` | HR samples + resting HR |
| `get_sleep_data` | Stages, scores, SpO2, HRV, respiration |
| `get_stress_data` | Stress levels, rest vs. active durations |
| `get_body_battery` | Body battery values |
| `get_body_composition` | Weight, BMI, body fat, muscle mass |
| `get_floors` | Floors ascended / descended |
| `get_hrv_data` | Overnight HRV |
| `get_training_readiness` | Training readiness scores |
| `get_recent_activities` | Most recent activities |
| `get_activities_by_date` | Activities in a date range, optional type filter |
| `get_last_n_days_summary` | Daily stats for the last N days |

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in GARMIN_EMAIL, GARMIN_PASSWORD, MCP_BEARER_TOKEN
set -a && source .env && set +a
python server.py
```

Hit `http://localhost:8000/mcp` to confirm the server is responding (you'll
get a 4xx from a plain GET — that's expected; MCP clients do POST/SSE).

## Deploy to Render

1. Push this repo to GitHub.
2. On [render.com](https://render.com): **New → Web Service → Connect GitHub →
   pick this repo**. Render auto-detects `render.yaml`.
3. In the Environment tab, fill in:
   - `GARMIN_EMAIL`
   - `GARMIN_PASSWORD`
   - `GARMINTOKENS_BASE64` (only if your account has MFA — see below)
   - `MCP_BEARER_TOKEN` is auto-generated; copy it from the dashboard.
4. Deploy. When the build is green, your URL is
   `https://<service>.onrender.com/mcp`.

### Handling MFA

The server runs non-interactively, so it can't prompt for an MFA code.
Bootstrap the tokens once locally:

```bash
pip install garminconnect
python bootstrap_tokens.py
# enter email + password + MFA code when asked
# copy the printed GARMINTOKENS_BASE64 blob into Render
```

The Garmin OAuth refresh token is good for ~1 year — set it once and forget.

## Connect to Claude

Desktop / web (`claude.ai`) → **Settings → Connectors → Add custom connector**:

- **URL**: `https://<service>.onrender.com/mcp`
- **Name**: `Garmin`
- **Authentication**: Bearer token → paste `MCP_BEARER_TOKEN`

Toggle the connector on inside a chat to use the tools.

## Security notes

- `MCP_BEARER_TOKEN` is the only thing between the internet and your
  Garmin data — generate a long random value and rotate if leaked.
- Garmin credentials are kept in env vars, never written to the repo.
- Token cache lives under `/tmp/.garminconnect` on the server; Render's
  filesystem is ephemeral, so the server re-uses cached tokens within an
  instance lifetime and re-bootstraps from `GARMINTOKENS_BASE64` on cold
  starts.
