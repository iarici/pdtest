"""Shared PD experiment app (Streamlit port of app/app.py).

Entry points (main.py, main_pilot.py) supply a setup-screen function and call
run(); everything from demographics to saved results lives here.

Streamlit re-runs this whole script on every interaction, so all session
state lives in st.session_state and every state change (randomness, timing,
balance updates) happens inside button callbacks — never during rendering —
so a rerun can never re-roll a bot action or double-count a round.

Module is named pd_app.py (not app.py) so it can't collide with the
original ../app/ folder when Streamlit puts the repo on sys.path.
"""

import base64
import csv
import io
import random
import re
import time
import zipfile
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st
import streamlit.components.v1 as components

import remote_store

from config import (
    STARTING_MONEY_EUR, UNPREDICTABLE_HONEST_PROB, PAYOFF_MATRIX,
    ROUNDS_BY_CONDITION, BFI2S, BFI2S_DOMAINS, BFI2S_REVERSE,
    IMG_W, IMG_H, IMAGE_FILES,
    TEXT, SUBTEXT, OUTCOME_STYLE, BTN_STYLES, BTN_TEXT,
)

try:
    from PIL import Image
    _PIL = True
except ImportError:
    _PIL = False

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
RESULTS_ROOT = BASE_DIR / "results"

PERSONALITY_PAGES = 3  # BFI-2-S: 30 items / 10 per page
PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # pid becomes a folder name — keep it path-safe
# participant-made code: initials (2-4 letters) + last two digits of their phone number, e.g. MR47
SELF_CODE_RE = re.compile(r"^[A-Za-z]{2,4}[0-9]{2}$")
# pilot data collected with the desktop app: IDs found there count as taken
PILOT_DATA_DIR = BASE_DIR.parent / "results" / "raw-data"
_PILOT_NAME_RE = re.compile(r"^(.+)_\d{8}_\d{6}(\.zip)?$")


# ── small helpers ──────────────────────────────────────────────────────────────

def darken(hex_color: str, amount: int = 20) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    return f"#{max(0,r-amount):02x}{max(0,g-amount):02x}{max(0,b-amount):02x}"


def action_name(a: str) -> str:
    return "Cooperate" if a == "C" else "Defect"


def expr_name(e: str) -> str:
    return e.capitalize()


def valid_pid(pid: str) -> bool:
    return bool(PID_RE.match(pid))


def valid_self_code(code: str) -> bool:
    return bool(SELF_CODE_RE.match(code))


def _pilot_ids() -> set[str]:
    if not PILOT_DATA_DIR.is_dir():
        return set()
    return {m.group(1).upper() for p in PILOT_DATA_DIR.iterdir() if (m := _PILOT_NAME_RE.match(p.name))}


def _try_claim(pid: str, taken: set[str]) -> bool:
    """Reserve one candidate ID. The GitHub registry (shared by every server and
    session) decides when it's configured; otherwise this server's results folder does."""
    if pid in taken:
        return False
    if remote_store.configured():
        status = remote_store.claim_id(pid)
        if status == "created":
            (RESULTS_ROOT / pid).mkdir(parents=True, exist_ok=True)
            return True
        if status == "exists":
            return False
        print(f"[id] could not reach the GitHub registry for {pid}; falling back to the local check")
    try:
        (RESULTS_ROOT / pid).mkdir(parents=True, exist_ok=False)   # atomic on this server
        return True
    except FileExistsError:
        return False


def claim_participant_id(requested: str) -> str:
    """Return a unique ID: the requested one, or requested-2, -3, ... if it's already used.

    IDs are upper-cased so MR47 and mr47 count as the same person.
    """
    base = requested.strip().upper()
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    taken = _pilot_ids()
    for n in range(1, 1000):
        cand = base if n == 1 else f"{base}-{n}"
        if _try_claim(cand, taken):
            return cand
    raise RuntimeError(f"no free participant ID for {base}")


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def btn(label: str, style: str, key: str, on_click: Callable | None = None,
        args: tuple = (), width: str = "content", **kw) -> bool:
    """Coloured button. Colour comes from CSS keyed on the 'sty-<style>-' key prefix."""
    return st.button(label, key=f"sty-{style}-{key}", on_click=on_click, args=args,
                     width=width, **kw)


# ── data model ─────────────────────────────────────────────────────────────────

@dataclass
class RoundRecord:
    condition_sequence: int
    condition: str
    round_index: int
    opponent_expression: str
    user_expression: str
    user_action: str
    bot_action: str
    user_delta_eur: int
    bot_delta_eur: int
    user_balance_eur: int
    bot_balance_eur: int
    reaction_time_ms: int
    expression_selection_rt_ms: int
    # per-round check-in, filled after the outcome screen (all −3…+3)
    rating_satisfaction: int = 0
    rating_trustworthiness: int = 0
    rating_mood: int = 0


@dataclass
class Session:
    participant_id: str = ""
    results_dir: Path | None = None
    conditions_order: list = field(default_factory=list)
    condition_index: int = 0
    condition_seq: int = 0
    condition: str = ""
    current_round: int = 0
    overall_round: int = 0
    total_rounds: int = 0
    user_balance: int = STARTING_MONEY_EUR
    bot_balance: int = STARTING_MONEY_EUR
    records: list = field(default_factory=list)
    current_opponent_expression: str = "happy"
    current_user_expression: str = "happy"
    current_bot_action: str = "C"
    unpredictable_bot_history: list = field(default_factory=list)
    stage: str = ""
    last_result: dict = field(default_factory=dict)
    last_deltas: tuple | None = None
    # perf_counter() stamps, set on first render of a stage (None = not yet shown)
    decision_start: float | None = None
    expr_start: float | None = None
    last_expr_rt_ms: int = 0
    hide_condition: bool = False
    personality_page: int = 1
    personality_responses: dict = field(default_factory=dict)
    demographics: dict = field(default_factory=dict)
    log: str = ""
    session_end_reason: str = ""
    saved_files: list[Path] = field(default_factory=list)
    upload_status: str = ""
    needs_save: bool = False   # set on finish, done while the finish screen shows a spinner


def S() -> Session:
    return st.session_state.pd


def _reset_state() -> None:
    st.session_state.pd = Session()


def _go(screen: str) -> None:
    st.session_state.screen = screen


def _log(text: str) -> None:
    S().log += text


# ── images ─────────────────────────────────────────────────────────────────────

@st.cache_resource
def _load_images() -> tuple[dict, dict]:
    """Base64 PNG data URIs at full and half size, keyed by expression."""
    big, small = {}, {}
    for expr, filename in IMAGE_FILES.items():
        path = ASSETS_DIR / filename
        if not path.exists():
            continue
        try:
            if _PIL:
                resample = getattr(Image, "Resampling", Image).LANCZOS
                src = Image.open(path)
                for store, size in ((big, (IMG_W, IMG_H)), (small, (IMG_W // 2, IMG_H // 2))):
                    buf = io.BytesIO()
                    src.resize(size, resample).save(buf, format="PNG")
                    store[expr] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            else:
                uri = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
                big[expr] = small[expr] = uri
        except Exception:
            pass
    return big, small


def _img_tag(expr: str, small: bool = False, width: int | None = None) -> str:
    big_imgs, small_imgs = _load_images()
    uri = (small_imgs if small else big_imgs).get(expr)
    w, h = (IMG_W // 2, IMG_H // 2) if small else (IMG_W, IMG_H)
    if width:
        w, h = width, round(width * IMG_H / IMG_W)
    if uri:
        return (f'<img src="{uri}" style="width:{w}px;max-width:100%;height:auto;'
                f'border-radius:12px;display:block;margin:0 auto;" alt="{expr}">')
    return (f'<div style="width:{w}px;max-width:100%;height:{h}px;margin:0 auto;display:flex;'
            f'align-items:center;justify-content:center;background:#eef1f7;border:1px solid #c5cde8;'
            f'border-radius:6px;font:bold 19px Helvetica;color:{TEXT};">[{expr.upper()}]</div>')


# ── styling ────────────────────────────────────────────────────────────────────

BODY_FONT = "'Nunito', system-ui, 'Segoe UI', sans-serif"
DISPLAY_FONT = "'Patrick Hand', 'Nunito', system-ui, sans-serif"   # hand-lettered: titles, numbers, buttons
GREEN, RED, ACCENT = "#2f7d4f", "#c0392b", "#23867a"
INK = "#2d2a26"      # pen-ink outlines + text
MUTED = "#6f665a"    # pencil-grey secondary text
LINE = INK
TINT = "#fffaf0"     # card stock
PAPER = "#f4ecdc"    # table / board paper
MUSTARD = "#f2c14e"
# subtle paper grain (SVG noise), tiled behind everything
_GRAIN = ("url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'>"
          "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/>"
          "<feColorMatrix values='0 0 0 0 .42  0 0 0 0 .33  0 0 0 0 .2  0 0 0 .09 0'/></filter>"
          "<rect width='100%' height='100%' filter='url(%23n)'/></svg>\")")


def _inject_css() -> None:
    rules = []
    for style, (fg, _shade, fs) in BTN_STYLES.items():
        txt = BTN_TEXT.get(style, "#fff")
        sel = f'[class*="st-key-sty-{style}-"] button'
        # board-game token: ink outline + hard offset shadow; presses into the table on click
        rules.append(f"{sel}{{background:{fg};color:{txt};border:2px solid {INK};border-radius:12px;"
                     f"padding:.45rem 1.4rem;box-shadow:4px 4px 0 {INK};"
                     f"transition:transform .08s ease, box-shadow .08s ease;}}")
        rules.append(f"{sel}:hover,{sel}:focus{{background:{fg};color:{txt};border-color:{INK};"
                     f"transform:translate(-1px,-1px);box-shadow:5px 5px 0 {INK};}}")
        rules.append(f"{sel}:active{{background:{fg};color:{txt};transform:translate(3px,3px);"
                     f"box-shadow:1px 1px 0 {INK};}}")
        rules.append(f"{sel} p{{font-family:{DISPLAY_FONT};font-size:{fs + 10}px;color:{txt};"
                     f"letter-spacing:.02em;white-space:nowrap;overflow:visible;text-overflow:clip;}}")
    html(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Patrick+Hand&family=Nunito:wght@500;600;700;800;900&display=swap');
    .stApp, .stApp p, .stApp label, .stApp li, .stApp input, .stApp textarea, .stApp button {{ font-family:{BODY_FONT}; }}
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ font-family:{DISPLAY_FONT}; font-weight:400; }}
    .stApp {{ background:{_GRAIN}, {PAPER}; background-attachment:fixed; }}
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {{ visibility:hidden; }}
    /* Streamlit's fixed top bar: empty here, but it covered the top of the page */
    [data-testid="stHeader"] {{ display:none !important; }}
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp p, .stApp label {{ color:{INK}; }}
    .block-container, [data-testid="stMainBlockContainer"] {{ padding-top:3rem !important; max-width:1000px; }}

    @keyframes pd-deal  {{ from {{opacity:0; transform:translateY(8px) rotate(-.6deg)}} to {{opacity:1; transform:none}} }}
    @keyframes pd-stamp {{ 0% {{opacity:0; transform:scale(1.6) rotate(-14deg)}}
                           100% {{opacity:.95; transform:scale(1) rotate(-8deg)}} }}

    .pd-h1 {{ font-family:{DISPLAY_FONT}; font-size:3.1rem; text-align:center; margin:0 0 .1rem; color:{INK};
              line-height:1.1; }}
    .pd-sub {{ color:{MUTED}; text-align:center; font-size:1.1rem; font-weight:700; margin:0 0 1.6rem; }}
    .pd-center {{ text-align:center; }}
    .pd-title {{ font-family:{DISPLAY_FONT}; text-align:center; font-size:2.1rem; margin:0 0 14px; color:{INK};
                 line-height:1.15; }}
    .pd-caption {{ color:{MUTED}; text-align:center; font-size:1.05rem; font-weight:700; margin:10px 0 14px; }}

    /* the game board */
    .st-key-stage {{ background:{TINT}; border:2px solid {INK}; border-radius:18px; padding:22px 26px 28px;
                     box-shadow:6px 6px 0 {INK}; animation:pd-deal .22s ease-out; }}
    .st-key-stage img {{ border:2px solid {INK}; border-radius:10px !important; }}

    /* round tracker: a row of board spaces */
    .pd-hud {{ display:flex; align-items:center; gap:18px; }}
    .pd-round {{ font-family:{DISPLAY_FONT}; font-size:1.6rem; color:{INK}; white-space:nowrap; line-height:1; }}
    .pd-round b {{ font-weight:400; background:{MUSTARD}; border:2px solid {INK}; border-radius:8px;
                   padding:0 8px; box-shadow:2px 2px 0 {INK}; }}
    .pd-round span {{ color:{MUTED}; }}
    .pd-segs {{ flex:1; display:flex; justify-content:space-between; align-items:center; gap:3px;
                 min-width:0; padding:0 4px; }}
    .pd-segs i {{ flex:none; width:14px; height:14px; border-radius:50%; border:2px solid {INK}; background:transparent; }}
    .pd-segs i.done {{ background:{INK}; }}
    .pd-segs i.now  {{ background:{MUSTARD}; transform:scale(1.25); }}
    [data-testid="stColumn"]:has(.st-key-sty-end-top) > [data-testid="stVerticalBlock"] {{ align-items:flex-end; }}

    /* score tags */
    .pd-bals {{ display:flex; gap:22px; margin:16px 0 22px; flex-wrap:wrap; }}
    .pd-bal {{ flex:1; min-width:240px; display:flex; align-items:center; gap:14px; background:{TINT};
               border:2px solid {INK}; border-radius:14px; padding:10px 18px; box-shadow:4px 4px 0 {INK}; }}
    .pd-bal.you {{ transform:rotate(-.6deg); background:#e6f2ef; }}
    .pd-bal.opp {{ transform:rotate(.6deg);  background:#fbe9e2; }}
    .pd-coin {{ flex:none; width:44px; height:44px; border-radius:50%; background:{MUSTARD};
                border:2px solid {INK}; box-shadow:inset 0 0 0 4px #f8d77f; display:flex; align-items:center;
                justify-content:center; font-family:{DISPLAY_FONT}; font-size:1.5rem; color:{INK}; }}
    .pd-bal-l {{ font-size:.8rem; font-weight:900; letter-spacing:.12em; text-transform:uppercase; color:{MUTED}; }}
    .pd-bal-v {{ font-family:{DISPLAY_FONT}; font-size:2.4rem; line-height:1; color:{INK};
                 display:flex; align-items:center; gap:10px; }}
    .pd-bal-s {{ font-size:.85rem; font-weight:700; color:{MUTED}; }}
    .pd-chip {{ font-family:{DISPLAY_FONT}; font-size:1.25rem; padding:0 9px; border:2px solid currentColor;
                border-radius:8px; background:{TINT}; transform:rotate(-3deg); display:inline-block; }}

    /* expression picker: cards laid on the table */
    .pd-pick {{ padding:10px; border-radius:14px; background:#fff; border:2px solid {INK};
                box-shadow:3px 3px 0 {INK}; margin-bottom:18px; transition:transform .15s; }}
    .pd-pick img {{ border-width:1.5px !important; }}
    [data-testid="stColumn"]:nth-child(1) .pd-pick {{ transform:rotate(-1.2deg); }}
    [data-testid="stColumn"]:nth-child(3) .pd-pick {{ transform:rotate(1.2deg); }}
    [data-testid="stColumn"] .pd-pick:hover {{ transform:translateY(-3px) rotate(0); }}

    /* outcome + recap */
    .pd-headline {{ font-family:{DISPLAY_FONT}; text-align:center; font-size:1.9rem; line-height:1.25;
                    margin:0 auto 18px; max-width:680px; color:{INK}; }}
    .pd-headline b {{ font-weight:400; background:linear-gradient(transparent 58%, {MUSTARD}88 58%); padding:0 2px; }}
    .pd-recap {{ display:flex; gap:22px; justify-content:center; flex-wrap:wrap; }}
    .pd-pcard {{ flex:1; max-width:300px; min-width:220px; background:#fff; border:2px solid {INK};
                 border-radius:14px; padding:12px 14px 14px; text-align:center; box-shadow:4px 4px 0 {INK}; }}
    .pd-recap .pd-pcard:first-child {{ transform:rotate(-.8deg); }}
    .pd-recap .pd-pcard:last-child  {{ transform:rotate(.8deg); }}
    .pd-pcard-who {{ font-size:.8rem; font-weight:900; letter-spacing:.12em; text-transform:uppercase;
                     color:{MUTED}; margin-bottom:8px; }}
    .pd-pcard-expr {{ font-size:1.05rem; color:{INK}; margin:10px 0 2px; }}
    .pd-stampwrap {{ position:relative; }}
    .pd-stamp {{ position:absolute; right:6px; bottom:8px; font-weight:900; font-size:1.15rem; letter-spacing:.14em;
                 text-transform:uppercase; padding:3px 12px; border:3px double currentColor; border-radius:6px;
                 background:rgba(255,250,240,.88); transform:rotate(-8deg); animation:pd-stamp .3s ease-out both; }}
    .pd-stamp.coop {{ color:{GREEN}; }}
    .pd-stamp.def  {{ color:{RED}; }}
    .pd-outcome {{ border:2px dashed {INK}; border-radius:14px; padding:12px 22px 14px; text-align:center;
                   margin:26px auto 22px; max-width:560px; }}
    .pd-outcome-d {{ font-family:{DISPLAY_FONT}; font-size:3.4rem; line-height:1; }}
    .pd-outcome-m {{ font-size:1.08rem; font-weight:600; margin-top:4px; }}
    .pd-outcome-m b {{ font-weight:900; font-size:1.15em; }}
    .pd-you {{ margin:22px auto 0; }}
    .pd-summary.compact .pd-headline {{ font-size:1.5rem; margin-bottom:12px; }}
    .pd-summary.compact .pd-you {{ margin-top:14px; }}
    .pd-summary.compact .pd-pcard {{ max-width:230px; min-width:180px; }}
    .pd-summary.compact .pd-stamp {{ animation:none; opacity:.95; font-size:.95rem; }}
    .pd-summary.compact .pd-outcome {{ margin:16px auto 30px; padding:8px 18px 10px; }}
    .pd-summary.compact .pd-outcome-d {{ font-size:2.4rem; }}
    .pd-mini {{ display:flex; align-items:center; gap:14px; background:#fff; border:2px solid {INK};
                border-radius:14px; padding:10px 16px; flex:1; min-width:250px; box-shadow:3px 3px 0 {INK}; }}
    .pd-mini img {{ width:96px !important; margin:0 !important; border-width:1.5px !important; }}
    .pd-mini-t {{ font-size:1.08rem; color:{INK}; line-height:1.4; }}

    /* rating sliders: larger question text, numbers and anchors */
    .pd-q {{ font-family:{DISPLAY_FONT}; font-size:1.7rem; color:{INK}; margin:30px 0 34px; line-height:1.2; }}
    .pd-anchors {{ display:flex; justify-content:space-between; color:{MUTED};
                   font-size:1rem; font-weight:800; margin-top:-18px; }}
    [data-testid="stSliderThumbValue"] {{ font-family:{DISPLAY_FONT}; font-size:1.4rem !important; }}
    [data-testid="stSliderTickBar"] {{ display:none !important; }}

    /* forms + questionnaire cards */
    [data-testid="stForm"], .st-key-bfi {{ background:{TINT}; border:2px solid {INK} !important;
                                           border-radius:16px; box-shadow:6px 6px 0 {INK}; }}
    .st-key-bfi {{ padding:16px 22px; }}
    /* text inputs + dropdowns: visible ink outline, teal on focus */
    .stApp [data-testid="stTextInputRootElement"], .stApp [data-testid="stNumberInputContainer"],
    .stApp [data-testid="stTextArea"] [data-baseweb="textarea"],
    .stApp [data-testid="stSelectbox"] [data-baseweb="select"] > div {{
        border-style:solid !important; border-width:2px !important; border-color:{INK} !important;
        border-radius:10px !important; background-color:#fff !important; }}
    .stApp [data-testid="stTextInputRootElement"] *, .stApp [data-testid="stNumberInputContainer"] [data-baseweb="input"] {{
        background-color:transparent !important; border-color:transparent !important; }}
    .stApp [data-testid="stTextInputRootElement"]:focus-within, .stApp [data-testid="stNumberInputContainer"]:focus-within,
    .stApp [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within {{
        border-color:{ACCENT} !important; box-shadow:3px 3px 0 {ACCENT}; }}

    /* consent page — a rulebook that flows on the page, no box */
    .pd-doc {{ max-width:760px; margin:0 auto; font-size:1.08rem; line-height:1.65; color:#3b362f; }}
    .pd-doc h4 {{ font-family:{DISPLAY_FONT}; font-size:1.9rem; margin:2rem 0 .5rem; color:{INK}; }}
    .pd-step {{ display:flex; align-items:center; gap:14px; padding:10px 16px; margin:9px 0;
                background:{TINT}; border:2px solid {INK}; border-radius:12px; box-shadow:3px 3px 0 {INK}; }}
    .pd-num {{ flex:none; width:34px; height:34px; border-radius:50%; background:{MUSTARD}; color:{INK};
               border:2px solid {INK}; font-family:{DISPLAY_FONT}; font-size:1.35rem;
               display:flex; align-items:center; justify-content:center; }}
    .pd-pay {{ display:flex; border-radius:10px; padding:9px 16px; margin:7px 0; font-weight:700;
               border:2px solid {INK}; }}
    .pd-pay:first-child {{ border:none; }}
    .pd-pay span:first-child {{ flex:1; }}
    @keyframes pd-in {{ from {{opacity:0; transform:translateY(10px)}} to {{opacity:1; transform:none}} }}
    .pd-new {{ animation:pd-in .45s ease-out both; }}
    .pd-ghost {{ opacity:.35; border-style:dashed; box-shadow:none; min-height:58px; }}
    .pd-ghost .pd-num {{ background:transparent; }}
    .pd-doc p b {{ font-weight:800; color:{INK}; }}
    /* form submit (demographics, pilot setup): same cardboard token as the other primary buttons */
    [data-testid="stFormSubmitButton"] button {{ background:{ACCENT}; color:#fff; border:2px solid {INK};
        border-radius:12px; box-shadow:4px 4px 0 {INK}; transition:transform .08s ease, box-shadow .08s ease; }}
    [data-testid="stFormSubmitButton"] button:hover, [data-testid="stFormSubmitButton"] button:focus {{
        background:{ACCENT}; color:#fff; border-color:{INK}; transform:translate(-1px,-1px); box-shadow:5px 5px 0 {INK}; }}
    [data-testid="stFormSubmitButton"] button:active {{ transform:translate(3px,3px); box-shadow:1px 1px 0 {INK}; }}
    [data-testid="stFormSubmitButton"] button p {{ font-family:{DISPLAY_FONT}; font-size:22px; color:#fff; }}
    /* participant-ID sticker (demographics + finish screens) */
    .pd-id {{ max-width:460px; margin:0 auto 22px; text-align:center; background:{MUSTARD}22;
              border:2px dashed {INK}; border-radius:14px; padding:10px 18px 12px; }}
    .pd-id-l {{ font-size:.8rem; font-weight:900; letter-spacing:.12em; text-transform:uppercase; color:{MUTED}; }}
    .pd-id-v {{ font-family:{DISPLAY_FONT}; font-size:2.4rem; line-height:1.1; color:{INK}; letter-spacing:.06em; }}
    .pd-id-note {{ font-size:.95rem; color:{INK}; margin:4px 0; }}
    .pd-id-s {{ font-size:.88rem; font-weight:700; color:{MUTED}; }}
    .pd-howto {{ background:{TINT}; border:2px solid {INK}; border-radius:14px; box-shadow:4px 4px 0 {INK};
                 padding:14px 20px; margin:0 auto 20px; font-size:1.02rem; line-height:1.55; color:{INK}; }}
    .pd-howto-t {{ font-family:{DISPLAY_FONT}; font-size:1.6rem; margin-bottom:4px; }}
    .pd-howto-ex {{ display:flex; align-items:center; justify-content:center; gap:8px; flex-wrap:wrap;
                    margin:10px 0 6px; font-family:{DISPLAY_FONT}; font-size:1.5rem; }}
    .pd-howto-ex span {{ background:#fff; border:2px solid {INK}; border-radius:8px; padding:0 10px; }}
    .pd-howto-ex i {{ font-style:normal; color:{MUTED}; }}
    .pd-rule {{ border:none; border-top:2px dashed {INK}; opacity:.35; margin:2.3rem 0 1rem; }}

    /* End Session: small paper button, never louder than the game buttons */
    [class*="st-key-sty-end-"] button {{ background:{TINT}; color:{MUTED}; border:2px solid {INK};
                                          border-radius:10px; padding:.2rem .9rem; box-shadow:2px 2px 0 {INK}; }}
    [class*="st-key-sty-end-"] button:hover, [class*="st-key-sty-end-"] button:focus
        {{ background:#fff; color:{RED}; border-color:{INK}; }}
    [class*="st-key-sty-end-"] button p {{ font-size:14px; font-weight:800; color:inherit; white-space:nowrap; }}
    {''.join(rules)}
    </style>""")


def _bind_cd_keys() -> None:
    """C / D keyboard shortcuts on the decision stage.

    Runs in a zero-height same-origin iframe and attaches one keydown listener
    to the parent page, which clicks the Cooperate/Defect button if present.
    The listener survives reruns; the doc flag prevents double-binding.
    """
    components.html("""<script>
    const doc = window.parent.document;
    if (!doc.__pdKeysBound) {
      doc.__pdKeysBound = true;
      doc.addEventListener('keydown', (e) => {
        const t = e.target;
        if (e.repeat || (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'))) return;
        const k = e.key.toLowerCase();
        const sel = k === 'c' ? '[class*="st-key-sty-cooperate-"] button'
                  : k === 'd' ? '[class*="st-key-sty-defect-"] button' : null;
        if (!sel) return;
        const b = doc.querySelector(sel);
        if (b) { e.preventDefault(); b.click(); }
      });
    }
    </script>""", height=0)


# ── flow entry points used by setup screens ────────────────────────────────────

def start_demographics(pid: str, conditions: list[str], hide_condition: bool = False,
                       claim: bool = True) -> None:
    """claim=True reserves a unique ID (adding -2, -3, ... on a clash).

    Going Back and re-entering the same code reuses this session's reservation
    instead of claiming a second one.
    """
    ss = st.session_state
    requested = pid.strip().upper()
    if not claim:
        final = pid
    elif ss.get("claimed_for") == requested:
        final = ss.claimed_id
    else:
        final = claim_participant_id(requested)
        ss.claimed_for, ss.claimed_id = requested, final
    ss.pending = {"pid": final, "requested_pid": requested, "conditions": list(conditions),
                  "hide_condition": hide_condition, "demographics": {}}
    _go("demographics")


def _id_badge(pid: str, requested: str | None = None) -> str:
    """Sticker showing the participant's ID; explains a -2/-3 suffix when one was added."""
    note = ""
    if requested and requested != pid:
        note = (f"<div class='pd-id-note'><b>{requested}</b> was already taken by another participant, "
                f"so your ID is <b>{pid}</b>.</div>")
    return (f"<div class='pd-id'><div class='pd-id-l'>Your participant ID</div>"
            f"<div class='pd-id-v'>{pid}</div>{note}"
            f"<div class='pd-id-s'>Please write it down. You will need it if you want to withdraw your data.</div></div>")


def back_to_setup() -> None:
    _go("setup")


# ── dev/testing shortcuts ──────────────────────────────────────────────────────

def dev_jump(target: str) -> None:
    demo_conditions = list(ROUNDS_BY_CONDITION)
    if target == "demographics":
        start_demographics("devtest", demo_conditions, claim=False)
    elif target == "consent":
        start_demographics("devtest", demo_conditions, claim=False)
        _consent_nav(1, 1)
        _go("consent")
    elif target == "game":
        _dev_start_session(demo_conditions)
        _prepare_next_round()
    elif target == "round_rating":
        _dev_start_session(demo_conditions)
        _prepare_next_round()
        _dev_fake_round()
    elif target == "personality":
        S().personality_page = 1
        _go("personality")
    elif target == "finish":
        _finish_session()


def _dev_start_session(conditions: list[str]) -> None:
    _start_session("devtest", conditions, hide_condition=False, demographics={})


def _dev_fake_round() -> None:
    s = S()
    s.records.append(RoundRecord(
        condition_sequence=1, condition=s.condition, round_index=1,
        opponent_expression="happy", user_expression="happy",
        user_action="C", bot_action="C", user_delta_eur=5, bot_delta_eur=5,
        user_balance_eur=s.user_balance, bot_balance_eur=s.bot_balance,
        reaction_time_ms=0, expression_selection_rt_ms=0,
    ))
    s.current_opponent_expression = s.current_user_expression = "happy"
    s.last_result = {"user_action": "C", "bot_action": "C", "user_delta": 5}
    s.stage = "round_rating"


# ── session start & round flow ─────────────────────────────────────────────────

def _start_session(pid: str, conditions: list[str], hide_condition: bool, demographics: dict) -> None:
    _reset_state()
    st.session_state.confirm_end = False
    s = S()
    s.participant_id = pid
    s.results_dir = RESULTS_ROOT / pid
    s.results_dir.mkdir(parents=True, exist_ok=True)
    s.conditions_order = list(conditions)
    s.hide_condition = hide_condition
    s.demographics = demographics or {}
    s.total_rounds = sum(ROUNDS_BY_CONDITION[c] for c in conditions)
    s.condition_index = 0
    s.condition_seq = 1
    s.condition = conditions[0]
    _go("game")


def _accept_consent() -> None:
    p = st.session_state.pending
    _start_session(p["pid"], p["conditions"], p["hide_condition"], p["demographics"])
    _prepare_next_round()


def _prepare_next_round() -> None:
    s = S()
    if s.current_round >= ROUNDS_BY_CONDITION[s.condition]:
        next_idx = s.condition_index + 1
        if next_idx < len(s.conditions_order):
            # roll straight into the next condition — no break screen
            s.condition_index = next_idx
            s.condition_seq += 1
            s.condition = s.conditions_order[next_idx]
            s.current_round = 0
            s.unpredictable_bot_history.clear()
            _prepare_next_round()
        else:
            _end_session("All conditions complete.")
        return
    s.current_round += 1
    s.overall_round += 1
    s.last_deltas = None
    s.current_opponent_expression = random.choice(["happy", "angry"])
    s.current_bot_action = _bot_action(s.condition, s.current_opponent_expression)
    if s.condition == "unpredictable":
        s.unpredictable_bot_history.append(s.current_bot_action)
    _set_stage("opponent_face")


def _bot_action(condition: str, expression: str) -> str:
    base = "C" if expression == "happy" else "D"
    flip = "D" if base == "C" else "C"
    if condition == "honest":
        return base
    if condition == "dishonest":
        return flip
    candidate = base if random.random() < UNPREDICTABLE_HONEST_PROB else flip
    hist = S().unpredictable_bot_history
    if len(hist) >= 2 and hist[-2] == hist[-1] == candidate:
        candidate = flip if candidate == base else base
    return candidate


def _set_stage(stage: str) -> None:
    s = S()
    s.stage = stage
    s.decision_start = None
    s.expr_start = None


def _select_expression(expr: str) -> None:
    s = S()
    if s.stage != "user_face":
        return
    start = s.expr_start if s.expr_start is not None else time.perf_counter()
    s.last_expr_rt_ms = int((time.perf_counter() - start) * 1000)
    s.current_user_expression = expr
    _set_stage("decision")


def _resolve_round(user_action: str) -> None:
    s = S()
    if s.stage != "decision":  # guards double clicks / repeated key presses
        return
    start = s.decision_start if s.decision_start is not None else time.perf_counter()
    reaction_time_ms = int((time.perf_counter() - start) * 1000)
    bot_action = s.current_bot_action
    user_delta, bot_delta = PAYOFF_MATRIX[(user_action, bot_action)]
    s.user_balance += user_delta
    s.bot_balance += bot_delta
    s.last_deltas = (user_delta, bot_delta)

    s.records.append(RoundRecord(
        condition_sequence=s.condition_seq,
        condition=s.condition,
        round_index=s.current_round,
        opponent_expression=s.current_opponent_expression,
        user_expression=s.current_user_expression,
        user_action=user_action,
        bot_action=bot_action,
        user_delta_eur=user_delta,
        bot_delta_eur=bot_delta,
        user_balance_eur=s.user_balance,
        bot_balance_eur=s.bot_balance,
        reaction_time_ms=reaction_time_ms,
        expression_selection_rt_ms=s.last_expr_rt_ms,
    ))

    if s.current_user_expression == "neutral":
        cong_tag = "NEU"
    else:
        cong_tag = "CON" if (s.current_user_expression == "happy") == (user_action == "C") else "INC"
    _log(
        f"R{s.current_round:02d} [{s.condition[:4]}] | "
        f"opp={s.current_opponent_expression[:3].upper()} "
        f"usr={s.current_user_expression[:3].upper()} | "
        f"you={user_action}  bot={bot_action} | "
        f"{cong_tag} | "
        f"{user_delta:+d} EUR | bal={s.user_balance} EUR | rt={reaction_time_ms}ms\n"
    )
    s.last_result = {"user_action": user_action, "bot_action": bot_action, "user_delta": user_delta}
    _set_stage("result")


def _submit_round_rating(rnd: int) -> None:
    s = S()
    if s.stage != "round_rating" or s.overall_round != rnd:
        return
    ss = st.session_state
    rec = s.records[-1]
    rec.rating_satisfaction = ss[f"rate_sat_{rnd}"]
    rec.rating_trustworthiness = ss[f"rate_trust_{rnd}"]
    rec.rating_mood = ss[f"rate_mood_{rnd}"]
    _prepare_next_round()


def _end_session(reason: str, skip_personality: bool = False) -> None:
    s = S()
    _log(f"\n--- SESSION END: {reason} ---\n")
    s.session_end_reason = reason
    s.stage = ""
    if skip_personality:
        _finish_session()
    else:
        s.personality_page = 1
        _go("personality")


# ── screens ────────────────────────────────────────────────────────────────────

def _demographics_screen() -> None:
    html("<h1 class='pd-h1'>A Few Quick Questions</h1>"
         "<p class='pd-sub'>Before we begin, please tell us a little about yourself.</p>")
    p = st.session_state.pending
    html(_id_badge(p["pid"], p.get("requested_pid")))
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        with st.form("demographics", border=True):
            age_str = st.text_input("Age")
            gender = st.selectbox("Gender", ["Female", "Male", "Non-binary", "Other",
                                             "Prefer not to say"], index=4)
            occupation = st.text_input("Occupation / student status")
            familiarity = st.selectbox("Familiarity with game theory / the prisoner's dilemma",
                                       ["Not at all familiar", "Somewhat familiar", "Very familiar"])
            submitted = st.form_submit_button("Continue", type="primary", width="stretch")
        if submitted:
            age_str = age_str.strip()
            occupation = occupation.strip()
            if not age_str.isdigit() or not (10 <= int(age_str) <= 100):
                st.error("Please enter a valid age.")
            elif not occupation:
                st.error("Please enter your occupation / student status.")
            else:
                st.session_state.pending["demographics"] = {
                    "age": int(age_str),
                    "gender": gender,
                    "occupation": occupation,
                    "game_theory_familiarity": familiarity,
                }
                _consent_nav(1, 1)
                _go("consent")
                st.rerun()
        btn("Back", "back", "demo", on_click=back_to_setup)


def _consent_nav(page: int, step: int = 0) -> None:
    st.session_state.consent_page = page
    st.session_state.consent_step = step


def _consent_screen() -> None:
    """Two pages: (1) game rules, revealed one step at a time; (2) study info + consent."""
    ss = st.session_state
    ss.setdefault("consent_page", 1)
    ss.setdefault("consent_step", 1)
    if ss.consent_page == 1:
        _rules_page(ss.consent_step)
    else:
        _study_page()


def _rules_page(shown: int) -> None:
    html("<h1 class='pd-h1'>How the Game Works</h1>"
         "<p class='pd-sub'>Part 1 of 2 &middot; Read each step, then continue.</p>")

    def _fmt(amount: int) -> str:
        return f"+€{amount}" if amount else f"€{amount}"

    steps = [
        "<b>Opponent's expression.</b> You see your opponent's facial expression, either happy or angry.",
        "<b>Your expression.</b> You choose which expression you want to show to your opponent. "
        "This is your choice, it does not have to match how you actually feel.",
        "<b>Your decision.</b> With both expressions visible side by side, you choose to "
        "Cooperate (C) or Defect (D), click the buttons or press the C / D keys.",
        "<b>Outcome.</b> You see what your opponent showed, what they actually chose, "
        "and how the round changed your balance.",
        "<b>Quick check-in.</b> You rate the outcome, the opponent's trustworthiness, "
        "and how you feel, using sliders.",
    ]
    shown = min(shown, len(steps))
    # only the newest step animates in; earlier ones stay put
    steps_html = "".join(
        f"<div class='pd-step{' pd-new' if i == shown else ''}'><div class='pd-num'>{i}</div><div>{t}</div></div>"
        for i, t in enumerate(steps[:shown], 1))
    pending = "".join(f"<div class='pd-step pd-ghost'><div class='pd-num'>{i}</div><div></div></div>"
                      for i in range(shown + 1, len(steps) + 1))

    doc = f"""<div class="pd-doc">
      <h4 style="margin-top:.4rem">The Task</h4>
      <p>You will play a series of decision-making rounds against a computer opponent.
      Both you and your opponent begin the session with a budget of €{STARTING_MONEY_EUR}.
      Your goal is to maximize your final budget.</p>
      <h4>How Each Round Works</h4>
      {steps_html}{pending}"""

    done = shown >= len(steps)
    if done:
        # pulled from PAYOFF_MATRIX so the display can never drift from what the game pays
        payoffs = [
            ("Both cooperate",              ("C", "C"), "#dcefdc", "#245c38"),
            ("You cooperate · they defect", ("C", "D"), "#f8dcd6", "#8f2a1f"),
            ("You defect · they cooperate", ("D", "C"), "#fbeccb", "#7a5512"),
            ("Both defect",                 ("D", "D"), "#ece6da", "#4a443b"),
        ]
        pay_html = (f"<div class='pd-pay' style='color:{MUTED};font-weight:700;padding-bottom:2px'>"
                    f"<span>Situation</span><span>You / Opponent</span></div>")
        for label, key, bg, fg in payoffs:
            u, o = PAYOFF_MATRIX[key]
            pay_html += (f"<div class='pd-pay' style='background:{bg};color:{fg}'>"
                         f"<span>{label}</span><span><b>{_fmt(u)} / {_fmt(o)}</b></span></div>")
        doc += f"<div class='pd-new'><h4>Payoffs</h4>{pay_html}</div>"
    html(doc + "</div>")

    _, left, _, right, _ = st.columns([0.6, 1, 2, 1, 0.6])
    with left:
        btn("Back", "back", "rules", on_click=back_to_setup, width="stretch")
    with right:
        if done:
            btn("Continue", "primary", "rules-next", on_click=_consent_nav, args=(2, len(steps)),
                width="stretch")
        else:
            btn("Next step", "primary", "rules-step", on_click=_consent_nav, args=(1, shown + 1),
                width="stretch")
    st.write("")


def _study_page() -> None:
    html("<h1 class='pd-h1'>About the Study</h1>"
         "<p class='pd-sub'>Part 2 of 2 &middot; Please read before you start.</p>")
    html(f"""<div class="pd-doc">
      <h4 style="margin-top:.4rem">Structure</h4>
      <p>The session has {sum(ROUNDS_BY_CONDITION[c] for c in st.session_state.pending["conditions"])} rounds in total
      and takes roughly 15-20 minutes.</p>
      <h4>At the End</h4>
      <p>After the final round you will answer a short personality questionnaire
      ({len(BFI2S)} statements about yourself). There are no right or wrong answers.</p>
      <h4>Your Data</h4>
      <p>Your responses are stored under your participant ID only and are used
      exclusively for research purposes.</p>
      <hr class="pd-rule">
      <p><b>By clicking Start, you agree to take part in the experiment. You can always withdraw at any time by clicking the End Session button.</b></p>
    </div>""")

    _, left, _, right, _ = st.columns([0.6, 1, 2, 1, 0.6])
    with left:
        btn("Back", "back", "consent", on_click=_consent_nav, args=(1, 99), width="stretch")
    with right:
        btn("Start", "primary", "consent-start", on_click=_accept_consent, width="stretch")
    st.write("")


def _set_confirm_end(value: bool) -> None:
    st.session_state.confirm_end = value


def _confirm_end_yes() -> None:
    st.session_state.confirm_end = False
    if st.session_state.screen == "game":
        _end_session("Ended early via End Session button.", skip_personality=True)


def _confirm_end_box() -> None:
    """Inline yes/no (replaces tk messagebox.askyesno)."""
    with st.container(border=True):
        st.warning("End the session now? Collected data will be saved.")
        c1, c2, _ = st.columns([1, 1, 3])
        c1.button("Yes, end session", key="end_yes", type="primary", width="stretch",
                  on_click=_confirm_end_yes)
        c2.button("Cancel", key="end_cancel", width="stretch",
                  on_click=_set_confirm_end, args=(False,))


def _top_bar() -> None:
    s = S()
    # one segment per round: played / current / still to come
    segs = "".join(
        f"<i class='{'done' if i < s.overall_round else 'now' if i == s.overall_round else ''}'></i>"
        for i in range(1, s.total_rounds + 1))

    hud, end = st.columns([6.5, 1.25], vertical_alignment="center")
    with hud:
        html(f"<div class='pd-hud'><div class='pd-round'>ROUND <b>{s.overall_round}</b>"
             f"<span> / {s.total_rounds}</span></div><div class='pd-segs'>{segs}</div></div>")
    with end:
        btn("End Session", "end", "top", on_click=_set_confirm_end, args=(True,))
    if st.session_state.get("confirm_end"):
        _confirm_end_box()

    def _card(label: str, amount: int, idx: int, cls: str) -> str:
        chip = ""
        if s.last_deltas is not None:
            d = s.last_deltas[idx]
            chip = f"<span class='pd-chip' style='color:{GREEN if d > 0 else RED}'>{_eur(d, sign=True)}</span>"
        return (f"<div class='pd-bal {cls}'><div class='pd-coin'>€</div><div>"
                f"<div class='pd-bal-l'>{label}</div>"
                f"<div class='pd-bal-v'>{_eur(amount)}{chip}</div>"
                f"<div class='pd-bal-s'>Started with {_eur(STARTING_MONEY_EUR)}</div></div></div>")

    html("<div class='pd-bals'>"
         + _card("Your balance", s.user_balance, 0, "you")
         + _card("Opponent's balance", s.bot_balance, 1, "opp")
         + "</div>")


def _stage_title(text: str) -> None:
    html(f"<div class='pd-title'>{text}</div>")


# ── plain-language recap helpers (outcome + rating screens) ────────────────────

def _eur(amount: int, sign: bool = False) -> str:
    if sign:
        return f"+€{amount}" if amount >= 0 else f"−€{abs(amount)}"
    return f"€{amount}" if amount >= 0 else f"−€{abs(amount)}"


def _a(expr: str) -> str:
    return f"{'an' if expr[0] in 'aeiou' else 'a'} {expr}"


def _opp_sentence(opp_expr: str, bot_action: str) -> str:
    act = "cooperated with you" if bot_action == "C" else "defected against you"
    return f"The opponent showed {_a(opp_expr)} face and <b>{act}</b>."


def _opp_shows(expr: str) -> str:
    return f"Opponent shows {_a(expr)} face"


def _you_sentence(user_expr: str, user_action: str) -> str:
    act = "cooperated" if user_action == "C" else "defected"
    return f"You showed {_a(user_expr)} face and <b>{act}</b>."


def _player_card(who: str, expr: str, action: str, width: int) -> str:
    cls = "coop" if action == "C" else "def"
    verb = "Cooperated" if action == "C" else "Defected"
    # the action is "rubber-stamped" onto the face card
    return (f"<div class='pd-pcard'><div class='pd-pcard-who'>{who}</div>"
            f"<div class='pd-stampwrap'>{_img_tag(expr, small=True, width=width)}"
            f"<span class='pd-stamp {cls}'>{verb}</span></div>"
            f"<div class='pd-pcard-expr'>Showed <b>{expr_name(expr)}</b></div></div>")


def _outcome_view(compact: bool = False) -> None:
    """Round summary: opponent's move above the cards, yours below, then the money.

    compact=True is the same summary, smaller and without animation (rating screen).
    """
    s = S()
    r = s.last_result
    # gaining nothing (sucker's payoff) is a loss, shown red like any other loss
    c = OUTCOME_STYLE["WON" if r["user_delta"] > 0 else "LOST"]
    width = 160 if compact else 220
    html(f"<div class='pd-summary{' compact' if compact else ''}'>"
         f"<div class='pd-headline'>{_opp_sentence(s.current_opponent_expression, r['bot_action'])}</div>"
         "<div class='pd-recap'>"
         + _player_card("Opponent", s.current_opponent_expression, r["bot_action"], width)
         + _player_card("You", s.current_user_expression, r["user_action"], width)
         + "</div>"
         f"<div class='pd-headline pd-you'>{_you_sentence(s.current_user_expression, r['user_action'])}</div>"
         f"<div class='pd-outcome' style='background:{c['bg']};color:{c['fg']}'>"
         f"<div class='pd-outcome-d'>{_eur(r['user_delta'], sign=True)}</div>"
         f"<div class='pd-outcome-m'>"
         + (f"You earned {_eur(r['user_delta'])} this round." if r["user_delta"] > 0
            else "You earned nothing this round.")
         + f" Your balance is now <b>{_eur(s.user_balance)}</b>.</div></div></div>")


def _rating_slider(question: str, lo_anchor: str, hi_anchor: str, key: str) -> None:
    html(f"<div class='pd-q'>{question}</div>")
    st.slider(question, -3, 3, 0, key=key, label_visibility="collapsed")
    html(f"<div class='pd-anchors'><span>{lo_anchor}</span><span>{hi_anchor}</span></div>")


def _centered_button(label: str, style: str, key: str, on_click: Callable, args: tuple = ()) -> None:
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        btn(label, style, key, on_click=on_click, args=args, width="stretch")


def _game_screen(show_log: bool) -> None:
    s = S()
    _top_bar()

    with st.container(key="stage"):
        if s.stage == "opponent_face":
            _stage_title(_opp_shows(s.current_opponent_expression))
            html(_img_tag(s.current_opponent_expression))
            st.write("")
            _centered_button("Continue", "primary", f"opp-{s.overall_round}",
                             on_click=_set_stage, args=("user_face",))

        elif s.stage == "user_face":
            if s.expr_start is None:
                s.expr_start = time.perf_counter()
            _stage_title(_opp_shows(s.current_opponent_expression))
            html(_img_tag(s.current_opponent_expression, width=320))
            html("<div class='pd-title' style='margin-top:30px'>Which expression do you want to show?</div>")
            cols = st.columns(3, gap="large")
            for col, expr in zip(cols, ("happy", "neutral", "angry")):
                with col:
                    html(f"<div class='pd-pick'>{_img_tag(expr, small=True)}</div>")
                    btn(expr_name(expr), expr, f"expr-{s.overall_round}", on_click=_select_expression,
                        args=(expr,), width="stretch")

        elif s.stage == "decision":
            if s.decision_start is None:
                s.decision_start = time.perf_counter()
            _stage_title("Make your decision")
            html("<div class='pd-recap'>"
                 f"<div class='pd-pcard'><div class='pd-pcard-who'>Opponent shows</div>"
                 f"{_img_tag(s.current_opponent_expression, small=True)}"
                 f"<div class='pd-pcard-expr'><b>{expr_name(s.current_opponent_expression)}</b></div></div>"
                 f"<div class='pd-pcard'><div class='pd-pcard-who'>You show</div>"
                 f"{_img_tag(s.current_user_expression, small=True)}"
                 f"<div class='pd-pcard-expr'><b>{expr_name(s.current_user_expression)}</b></div></div>"
                 "</div>")
            html("<p class='pd-caption'>Press <b>C</b> to cooperate or <b>D</b> to defect</p>")
            _, c1, c2, _ = st.columns([1, 2, 2, 1])
            with c1:
                btn("Cooperate  [C]", "cooperate", "dec", on_click=_resolve_round, args=("C",),
                    width="stretch")
            with c2:
                btn("Defect  [D]", "defect", "dec", on_click=_resolve_round, args=("D",),
                    width="stretch")
            _bind_cd_keys()

        elif s.stage == "result":
            _outcome_view()
            _centered_button("Continue", "primary", f"res-{s.overall_round}",
                             on_click=_set_stage, args=("round_rating",))

        elif s.stage == "round_rating":
            _outcome_view(compact=True)
            _stage_title("Quick check-in")
            n = s.overall_round
            _, mid, _ = st.columns([1, 5, 1])
            with mid:
                # keys include the round number so each round starts back at 0
                _rating_slider("How satisfied are you with this outcome?",
                               "Very dissatisfied", "Very satisfied", f"rate_sat_{n}")
                _rating_slider("How trustworthy does the opponent seem right now?",
                               "Not at all trustworthy", "Very trustworthy", f"rate_trust_{n}")
                _rating_slider("How do you feel right now?",
                               "Very negative", "Very positive", f"rate_mood_{n}")
            st.write("")
            _centered_button("Continue", "primary", f"rate-{n}",
                             on_click=_submit_round_rating, args=(n,))

    if show_log:
        html(f"<p style='color:{SUBTEXT};font-size:11px;font-weight:700;margin:10px 0 2px'>SESSION LOG</p>")
        with st.container(height=160, border=True):
            st.code(s.log or " ", language=None)


def _submit_personality(page: int) -> None:
    s = S()
    if st.session_state.screen != "personality" or s.personality_page != page:
        return
    offset = (page - 1) * 10
    for i in range(len(BFI2S[offset:offset + 10])):
        n = offset + i + 1
        s.personality_responses[n] = st.session_state[f"bfi_{n}"]
    if page < PERSONALITY_PAGES:
        s.personality_page = page + 1
    else:
        _finish_session()


def _personality_screen() -> None:
    s = S()
    page = s.personality_page
    offset = (page - 1) * 10
    items = BFI2S[offset:offset + 10]

    html("<h1 class='pd-h1'>About You</h1>"
         f"<p class='pd-sub'>Page {page} of {PERSONALITY_PAGES}  —  I am someone who …</p>")
    legend = "".join(
        f"<div style='text-align:center;font-size:12px;padding:0 10px'><b>{n}</b><br>{lbl}</div>"
        for n, lbl in enumerate(["Disagree<br>strongly", "Disagree<br>a little",
                                 "Neutral;<br>no opinion", "Agree<br>a little",
                                 "Agree<br>strongly"], 1))
    html(f"<div style='display:flex;justify-content:center;background:#eef1f7;border-radius:8px;"
         f"padding:8px;margin:0 auto 14px;width:fit-content;color:{TEXT}'>{legend}</div>")

    with st.container(key="bfi"):
        for i, item in enumerate(items):
            n = offset + i + 1
            q, sl = st.columns([3, 2], vertical_alignment="center")
            with q:
                html(f"<span style='color:{ACCENT};font-weight:800'>{n}.</span> {item}")
            with sl:
                st.slider(item, 1, 5, 3, key=f"bfi_{n}", label_visibility="collapsed")

    is_last = page >= PERSONALITY_PAGES
    _, right = st.columns([4, 1])
    with right:
        btn("Submit" if is_last else "Next", "primary", f"bfi-p{page}",
            on_click=_submit_personality, args=(page,), width="stretch")


# ── saving & wrap-up ───────────────────────────────────────────────────────────

def _write_csv(path: Path, fieldnames: list, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _save_results() -> str | None:
    s = S()
    if not s.records or s.results_dir is None:
        return None
    now = datetime.now()
    stem = f"{s.participant_id}_{now.strftime('%Y%m%d_%H%M%S')}"
    d = s.results_dir

    _write_csv(d / f"{stem}.csv", [f.name for f in fields(RoundRecord)],
               [r.__dict__ for r in s.records])

    # BFI-2-S scoring (1-5 scale, 6 items/domain), reverse(x) = 6 - x.
    # emotional_stability = 6 - negative_emotionality to match the legacy TIPI
    # column polarity analyze_results.py assumes.
    pr = s.personality_responses

    def _item(i: int) -> int:
        v = pr.get(i, 3)
        return 6 - v if i in BFI2S_REVERSE else v

    bfi_scores = {}
    if pr:
        for dim, items in BFI2S_DOMAINS.items():
            bfi_scores[dim] = round(sum(_item(i) for i in items) / len(items), 2)
        bfi_scores["emotional_stability"] = round(6 - bfi_scores.pop("negative_emotionality"), 2)

    summary_row = {
        "participant_id": s.participant_id,
        "conditions_order": "|".join(s.conditions_order),
        "hide_condition": s.hide_condition,
        "rounds_played": len(s.records),
        "final_user_balance_eur": s.user_balance,
        "final_bot_balance_eur": s.bot_balance,
        "created_at": now.isoformat(timespec="seconds"),
    }
    _write_csv(d / f"{stem}_summary.csv", list(summary_row), [summary_row])

    _write_csv(d / f"{stem}_ratings.csv", [
        "condition_sequence", "condition", "round_index",
        "user_expression", "user_action",
        "opponent_expression", "bot_action", "reaction_time_ms",
        "satisfaction", "trustworthiness", "mood"], [{
            "condition_sequence": r.condition_sequence,
            "condition": r.condition,
            "round_index": r.round_index,
            "user_expression": r.user_expression,
            "user_action": r.user_action,
            "opponent_expression": r.opponent_expression,
            "bot_action": r.bot_action,
            "reaction_time_ms": r.reaction_time_ms,
            "satisfaction": r.rating_satisfaction,
            "trustworthiness": r.rating_trustworthiness,
            "mood": r.rating_mood,
        } for r in s.records])

    bfi_row = {"participant_id": s.participant_id, **pr, **bfi_scores}
    _write_csv(d / f"{stem}_bfi.csv", list(bfi_row), [bfi_row])

    if s.demographics:
        demo_row = {"participant_id": s.participant_id, **s.demographics}
        _write_csv(d / f"{stem}_demographics.csv", list(demo_row), [demo_row])

    return stem


def _finish_session() -> None:
    """Switch to the finish screen at once; it saves (and uploads) while showing a spinner.

    Saving still happens in this same script run, right after the click, so
    nothing depends on the browser staying open - but the participant sees the
    page change immediately instead of a frozen screen they might click again.
    """
    S().needs_save = True
    _go("finish")


def _save_and_upload() -> None:
    s = S()
    s.needs_save = False
    stem = _save_results()
    if not stem:
        return
    d = s.results_dir
    log_path = d / f"{stem}_log.txt"
    log_path.write_text(s.log, encoding="utf-8")
    files = [p for p in (d / f"{stem}.csv", d / f"{stem}_ratings.csv", d / f"{stem}_bfi.csv",
                         d / f"{stem}_demographics.csv", d / f"{stem}_summary.csv", log_path)
             if p.exists()]
    # plain CSV files, no zipping/encryption in the web version (the data repo is private)
    s.saved_files = files
    _upload_results(files)


def _upload_results(files: list[Path]) -> None:
    """Hosted deployments: copy the results to the private GitHub data repo."""
    s = S()
    if not remote_store.configured():
        s.upload_status = "Remote upload not configured (results kept on this server only)."
        return
    msgs = []
    for p in files:
        ok, msg = remote_store.upload(p, p.name, s.participant_id)
        msgs.append(("OK " if ok else "FAILED ") + msg)
        print(f"[results] {msgs[-1]}")
    s.upload_status = "  \n".join(msgs)


def _finish_screen(show_log: bool) -> None:
    s = S()
    html(f"""<div style="text-align:center;margin-top:12vh">
      <h1 class="pd-h1">Session Complete</h1>
      <p class="pd-sub">Thank you for participating.</p>
      <div class="pd-bal you" style="display:inline-flex;flex:none;padding:18px 36px;text-align:left">
        <div class="pd-coin" style="width:56px;height:56px;font-size:1.6rem">€</div>
        <div>
          <div class="pd-bal-l">Final balance</div>
          <div class="pd-bal-v" style="font-size:2.8rem">{_eur(s.user_balance)}</div>
          <div class="pd-bal-s">Started with {_eur(STARTING_MONEY_EUR)}</div>
        </div>
      </div>
      <div style="font-size:.95rem;font-weight:600;color:{MUTED};margin-top:16px">{s.session_end_reason}</div>
    </div>""")
    html(_id_badge(s.participant_id))
    st.write("")
    if s.needs_save:
        _, mid, _ = st.columns([1, 1, 1])
        with mid, st.spinner("Saving your results... please keep this page open."):
            _save_and_upload()
        st.rerun()   # redraw without the spinner, with the download button / upload status
    # experimenter build only: grab this session's files straight from the browser
    files = [p for p in s.saved_files if p.exists()]
    if show_log and files:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:   # plain bundle, no password
            for p in files:
                zf.write(p, p.name)
        _, mid, _ = st.columns([1, 1, 1])
        with mid:
            st.download_button("Download results", buf.getvalue(), file_name=f"{files[0].stem}.zip",
                               mime="application/zip", width="stretch")
    if show_log and s.upload_status:
        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            st.caption(s.upload_status)


# ── main entry ─────────────────────────────────────────────────────────────────

def run(setup_screen: Callable[[], None], show_log: bool = False) -> None:
    """Render the current screen. `setup_screen` draws the entry point's own first page."""
    st.set_page_config(page_title="Decision Game", layout="wide")
    _inject_css()
    ss = st.session_state
    if "pd" not in ss:
        _reset_state()
    ss.setdefault("screen", "setup")
    ss.setdefault("pending", None)

    screen = ss.screen
    if screen == "setup":
        setup_screen()
    elif screen == "demographics":
        _demographics_screen()
    elif screen == "consent":
        _consent_screen()
    elif screen == "game":
        _game_screen(show_log)
    elif screen == "personality":
        _personality_screen()
    elif screen == "finish":
        _finish_screen(show_log)
