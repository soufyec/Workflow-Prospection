"""
Two-step Gmail OAuth2 helper (PKCE-safe).

  Step 1 — generate URL:
    python auth_gmail.py

  Step 2 — complete auth with the code Google gave you:
    python auth_gmail.py <code>
"""

import base64
import hashlib
import json
import os
import secrets
import sys

import requests

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]
CREDENTIALS_PATH = "credentials/credentials.json"
TOKEN_PATH = "credentials/token.json"
_STATE_FILE = ".oauth_state.json"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def _load_client_config():
    with open(CREDENTIALS_PATH) as f:
        data = json.load(f)
    cfg = data.get("installed") or data.get("web")
    return cfg["client_id"], cfg["client_secret"], cfg["token_uri"]


def step1_generate_url():
    client_id, _, _ = _load_client_config()

    # Generate PKCE pair
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + "&".join(
        f"{k}={requests.utils.quote(str(v))}" for k, v in params.items()
    )

    with open(_STATE_FILE, "w") as f:
        json.dump({"code_verifier": code_verifier, "client_id": client_id}, f)

    print("\n" + "=" * 60)
    print("  Abre esta URL en tu navegador:")
    print("=" * 60)
    print(f"\n  {auth_url}\n")
    print("=" * 60)
    print("\n  Luego ejecuta:")
    print("    python auth_gmail.py <código>\n")


def step2_complete(code: str):
    if not os.path.exists(_STATE_FILE):
        print("  [ERROR] No hay estado guardado. Ejecuta primero: python auth_gmail.py")
        sys.exit(1)

    with open(_STATE_FILE) as f:
        state = json.load(f)

    client_id, client_secret, token_uri = _load_client_config()

    resp = requests.post(token_uri, data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": state["code_verifier"],
    })
    resp.raise_for_status()
    token_data = resp.json()

    # Format compatible with google.oauth2.credentials
    creds_json = {
        "token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": token_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": SCOPES,
        "expiry": None,
    }
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(creds_json, f)
    os.remove(_STATE_FILE)
    print(f"  Token guardado en {TOKEN_PATH}")
    print("  Ahora ejecuta: python main.py --step draft")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        step1_generate_url()
    elif len(sys.argv) == 2:
        step2_complete(sys.argv[1].strip())
    else:
        print(__doc__)
        sys.exit(1)
