"""Copy finished results to a private GitHub repo (for hosted deployments).

Hosted Streamlit servers have no persistent disk, so after a session is saved
locally the results zip is also pushed to a separate, private data repo through
the GitHub Contents API. Configure it in Streamlit secrets
(`.streamlit/secrets.toml` locally, or the app's Settings -> Secrets on
Streamlit Community Cloud):

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
from pathlib import Path

import requests
import streamlit as st

API = "https://api.github.com"


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


def upload(local: Path, remote_name: str, participant_id: str) -> tuple[bool, str]:
    """Upload one file to <folder>/<participant_id>/<remote_name>. Returns (ok, message)."""
    cfg = _settings()
    if cfg is None:
        return False, "GitHub upload not configured"
    folder = cfg.get("folder", "results").strip("/")
    path = f"{folder}/{participant_id}/{remote_name}"
    url = f"{API}/repos/{cfg['repo']}/contents/{path}"
    headers = {"Authorization": f"Bearer {cfg['token']}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    body = {"message": f"Add results {participant_id}/{remote_name}",
            "content": base64.b64encode(local.read_bytes()).decode(),
            "branch": cfg.get("branch", "main")}

    last = ""
    for attempt in range(3):
        try:
            r = requests.put(url, json=body, headers=headers, timeout=20)
            if r.status_code in (200, 201):
                return True, f"uploaded to {cfg['repo']}/{path}"
            if r.status_code == 422 and "sha" in r.text:
                # a file with this exact name already exists, never overwrite data
                return False, f"{path} already exists in {cfg['repo']}"
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (401, 403, 404):
                break          # bad token / no access / wrong repo name: retrying won't help
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))
    return False, f"upload failed ({last})"
