"""Pilot entry point (Streamlit port of app/main_pilot.py).

Fixed condition set, participant-made ID on the first page, no visible log
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
    pd_app.html("<div style='margin-top:6vh'></div>"
                "<h1 class='pd-h1' style='font-size:2.6rem'>Decision Game</h1>"
                "<p class='pd-sub' style='margin-bottom:1.6rem'>A decision-making experiment</p>")
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        # the ID rule, front and centre: people must be able to recreate their code later
        pd_app.html(
            "<div class='pd-howto'>"
            "<div class='pd-howto-t'>Create your participant ID</div>"
            "Your ID is your <b>initials</b> followed by the <b>last two digits of your phone number</b>. "
            "</div>")
        # a form so pressing Enter in the ID field submits
        with st.form("pilot_setup", border=True):
            pid = st.text_input("Participant ID", placeholder="e.g. MR47", max_chars=6)
            submitted = st.form_submit_button("Start Session", type="primary", width="stretch")
        if submitted:
            pid = pid.strip().replace(" ", "")
            if not pid:
                st.error("Please enter your participant ID.")
            elif not pd_app.valid_self_code(pid):
                st.error("Your ID should be 2-4 letters (your initials) followed by 2 digits "
                         "(the last two digits of your phone number), for example MR47.")
            else:
                conditions = list(PILOT_CONDITIONS)
                if PILOT_RANDOMIZE:
                    random.shuffle(conditions)
                with st.spinner("Checking your ID..."):
                    pd_app.start_demographics(pid, conditions, hide_condition=PILOT_HIDE_COND)
                st.rerun()


pd_app.run(setup_screen, show_log=False)
