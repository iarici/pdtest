"""Experimenter entry point (Streamlit port of app/main.py).

Runtime condition setup, dev-jump shortcuts, visible session log.
Run with:  streamlit run main.py   (from inside web_app/)
"""

import random

import streamlit as st

import pd_app
from config import ROUNDS_BY_CONDITION


def setup_screen() -> None:
    pd_app.html("<h1 class='pd-center'>Face-Driven Decision Game</h1>"
                "<p class='pd-sub'>Configure the session below, then click Start.</p>")
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.container(border=True):
        tab_session, tab_exp, tab_dev = st.tabs(["Session", "Experimenter", "Dev Jump"])

        with tab_session:
            pid = st.text_input("Participant ID", key="setup_pid")

        with tab_exp:
            st.markdown("**Conditions to run**  (run in listed order)")
            selected_flags = {
                cond: st.checkbox(f"{cond.capitalize()}  ({n} rounds)", value=(i == 0),
                                  key=f"setup_cond_{cond}")
                for i, (cond, n) in enumerate(ROUNDS_BY_CONDITION.items())
            }
            randomize = st.checkbox("Randomize condition order", key="setup_randomize")
            hide_cond = st.checkbox("Hide condition name from participant", key="setup_hide")

        with tab_dev:
            st.caption("Skip straight to a screen for QA (uses a fake 'devtest' session)")
            targets = [("Demographics", "demographics"), ("Consent", "consent"),
                       ("Game (round 1)", "game"), ("Round-end ratings", "round_rating"),
                       ("Personality (BFI-2-S)", "personality"), ("Finish screen", "finish")]
            cols = st.columns(2)
            for i, (label, target) in enumerate(targets):
                with cols[i % 2]:
                    pd_app.btn(label, "back", f"dev-{target}", on_click=pd_app.dev_jump,
                               args=(target,), width="stretch")

        if pd_app.btn("Start Session", "primary", "setup-start", width="stretch"):
            pid = pid.strip()
            selected = [c for c, on in selected_flags.items() if on]
            if not pid:
                st.error("Please enter a participant ID (Session tab).")
            elif not pd_app.valid_pid(pid):
                st.error("Participant ID may only contain letters, digits, '-' and '_'.")
            elif not selected:
                st.error("Please select at least one condition (Experimenter tab).")
            else:
                if randomize:
                    random.shuffle(selected)
                pd_app.start_demographics(pid, selected, hide_condition=hide_cond)
                st.rerun()


pd_app.run(setup_screen, show_log=True)
