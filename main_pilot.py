"""Pilot entry point (Streamlit port of app/main_pilot.py).

Fixed condition set, simple participant-ID setup screen, no visible log
(the log is still saved with the results).
Run with:  streamlit run main_pilot.py   (from inside web_app/)
"""

import random

import streamlit as st

import pd_app

# ── Pilot configuration ────────────────────────────────────────────────────────
PILOT_CONDITIONS = ["honest", "dishonest", "unpredictable"]  # order matters if not randomized
PILOT_RANDOMIZE  = True   # shuffle condition order per participant
PILOT_HIDE_COND  = True   # hide condition name from participant
# ──────────────────────────────────────────────────────────────────────────────


def setup_screen() -> None:
    pd_app.html("<div style='margin-top:12vh'></div>"
                "<h1 class='pd-center' style='font-size:2.6rem'>Face-Driven Decision Game</h1>"
                "<p class='pd-sub' style='margin-bottom:2rem'>A decision-making experiment</p>")
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        # a form so pressing Enter in the ID field submits
        with st.form("pilot_setup", border=True):
            pid = st.text_input("Participant ID")
            submitted = st.form_submit_button("Start Session", type="primary", width="stretch")
        if submitted:
            pid = pid.strip()
            if not pid:
                st.error("Please enter a participant ID.")
            elif not pd_app.valid_pid(pid):
                st.error("Participant ID may only contain letters, digits, '-' and '_'.")
            else:
                conditions = list(PILOT_CONDITIONS)
                if PILOT_RANDOMIZE:
                    random.shuffle(conditions)
                pd_app.start_demographics(pid, conditions, hide_condition=PILOT_HIDE_COND)
                st.rerun()


pd_app.run(setup_screen, show_log=False)
