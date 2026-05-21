"""One-shot helper: log into Garmin locally (handling MFA), then print a base64
blob you can paste into Render's GARMINTOKENS_BASE64 env var.

Usage:
    GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=... python bootstrap_tokens.py
"""

from __future__ import annotations

import base64
import io
import os
import tarfile
import tempfile

from garminconnect import Garmin


def main() -> None:
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.environ.get("GARMIN_PASSWORD") or input("Garmin password: ")

    with tempfile.TemporaryDirectory() as tokendir:
        client = Garmin(email=email, password=password, prompt_mfa=lambda: input("MFA code: "))
        client.login(tokendir)
        print(f"Logged in as {client.display_name} ({client.full_name})")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(tokendir, arcname=".")
        blob = base64.b64encode(buf.getvalue()).decode()

    print("\n=== GARMINTOKENS_BASE64 ===")
    print(blob)
    print("===========================\n")
    print("Set this as the GARMINTOKENS_BASE64 env var on Render.")


if __name__ == "__main__":
    main()
