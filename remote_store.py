"""Copy finished results to a private GitHub repo (for hosted deployments).

Hosted Streamlit servers have no persistent disk, so after a session is saved
locally the result files are also pushed to a separate, private data repo
through the GitHub Contents API. The same repo doubles as the registry of used
participant IDs: claiming an ID creates <folder>/<ID>/_claimed.txt, and GitHub
refuses to create a file that already exists, so two people can never end up
with the same ID. Configure it in Streamlit secrets (`.streamlit/secrets.toml`
locally, or the app's Settings -> Secrets on Streamlit Community Cloud):

    [github]
    token  = "github_pat_..."              # fine-grained token, Contents: read & write on the data repo only
    repo   = "your-user/pd-experiment-data"
    branch = "main"                        # optional, default "main"
    folder = "results"                     # optional, default "results"

Without that section, uploading is skipped and only the local copy is kept.
"""

from __future__ import annotations

import base64
import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

API = "https://api.github.com"
CLAIM_FILE = "_claimed.txt"


def _settings() -> dict | None:
    try:
        cfg = st.secrets.get("github")
    except Exception:          # no secrets file at all
        return None
    if not cfg or not cfg.get("token") or not cfg.get("repo"):
        return None
    return dict(cfg)


def configured() -> bool:
    return _settings() is not None


def _create(rel_path: str, data: bytes, message: str) -> tuple[str, str]:
    """Create <folder>/<rel_path>; never overwrites.

    Returns (status, detail) with status "created", "exists" or "error".
    """
    cfg = _settings()
    if cfg is None:
        return "error", "GitHub upload not configured"
    path = f"{cfg.get('folder', 'results').strip('/')}/{rel_path}"
    url = f"{API}/repos/{cfg['repo']}/contents/{path}"
    headers = {"Authorization": f"Bearer {cfg['token']}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    body = {"message": message, "content": base64.b64encode(data).decode(),
            "branch": cfg.get("branch", "main")}

    last = ""
    for attempt in range(3):
        try:
            r = requests.put(url, json=body, headers=headers, timeout=20)
            if r.status_code in (200, 201):
                return "created", f"{cfg['repo']}/{path}"
            if r.status_code == 422 and "sha" in r.text:
                return "exists", f"{cfg['repo']}/{path}"
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (401, 403, 404):
                break          # bad token / no access / wrong repo name: retrying won't help
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))
    return "error", last


def upload(local: Path, remote_name: str, participant_id: str) -> tuple[bool, str]:
    """Upload one file to <folder>/<participant_id>/<remote_name>. Returns (ok, message)."""
    status, detail = _create(f"{participant_id}/{remote_name}", local.read_bytes(),
                             f"Add results {participant_id}/{remote_name}")
    if status == "created":
        return True, f"uploaded to {detail}"
    if status == "exists":
        return False, f"{detail} already exists"     # never overwrite data
    return False, f"upload failed ({detail})"


def claim_id(participant_id: str) -> str:
    """Reserve an ID in the data repo: "created" (it's yours), "exists" (taken) or "error"."""
    stamp = datetime.now().isoformat(timespec="seconds")
    status, _ = _create(f"{participant_id}/{CLAIM_FILE}", f"claimed {stamp}\n".encode(),
                        f"Claim participant ID {participant_id}")
    return status
