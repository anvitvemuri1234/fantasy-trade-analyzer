import streamlit as st
import pandas as pd
from sleeper import parse_league
from engine import (
    prepare_roster,
    fast_lineup_score,
    find_2team_trades
)

# Page configuration
st.set_page_config(
    page_title="Fantasy Trades",
    page_icon="",
    layout="wide"
)

# Custom header styling
st.title("Fantasy Football Trade Finder")

# Sidebar: League Configuration
with st.sidebar:
    st.header("League Setup")
    league_id = st.text_input("Sleeper League ID", value="1382555726110539776")
    load_button = st.button("Sync League Data", use_container_width=True)

# Cache data loading so the app runs instantly on button clicks
@st.cache_data(ttl=600, show_spinner=False)
def load_league_data(lid: str):
    return parse_league(lid)

if "league_meta" not in st.session_state or load_button:
    with st.spinner("Connecting to Sleeper API & loading rosters..."):
        try:
            meta, teams = load_league_data(league_id)
            st.session_state.league_meta = meta
            st.session_state.teams = teams
            st.success(f"Synced: {meta.get('name', 'Fantasy League')}")
        except Exception as e:
            st.error(f"Error loading league: {e}")
            st.stop()

teams = st.session_state.get("teams", [])
if not teams:
    st.info("Enter a League ID in the sidebar to begin.")
    st.stop()

# ----------------------------------------------------
# 1. Team & Anchor Player Selection
# ----------------------------------------------------
col_team, col_partner = st.columns(2)

owner_names = [t["owner"] for t in teams]

with col_team:
    st.subheader("Your Team")
    # Default to anvit if present
    default_idx = next((i for i, name in enumerate(owner_names) if name.lower() == "anvit"), 0)
    my_owner = st.selectbox("Select Your Manager Profile", owner_names, index=default_idx)
    my_team = next(t for t in teams if t["owner"] == my_owner)
    
    # Pre-cache and calculate starting lineup baseline
    prepare_roster(my_team["players"])
    my_baseline = fast_lineup_score(my_team["players"])
    st.metric(label="Optimal Starting Lineup Baseline", value=f"{my_baseline:.2f} pts/wk")

with col_partner:
    st.subheader("Target Partner")
    partner_options = ["League-Wide (Scan All Teams)"] + [t["owner"] for t in teams if t["owner"] != my_owner]
    selected_partner_option = st.selectbox("Who are you trading with?", partner_options)

# Optional Anchor Player
st.markdown("---")
skill_players = [p for p in my_team["players"] if p.get("position") in ["QB", "RB", "WR", "TE"]]
player_options = {"Search All Players (No Anchor)": None}
for p in skill_players:
    label = f"{p['name']} ({p['position']}) — Proj: {p.get('live_proj', 0.0):.1f} pts"
    player_options[label] = p["player_id"]

selected_anchor_label = st.selectbox(
    "Target Player to Trade Away (Anchor):",
    list(player_options.keys())
)
anchor_id = player_options[selected_anchor_label]

# Trade Tuning Sliders
with st.expander("⚙️ Advanced Optimization Parameters", expanded=False):
    col_tune1, col_tune2 = st.columns(2)
    with col_tune1:
        min_gain = st.slider("Minimum Starting Lineup Gain for You (pts/wk)", 0.0, 3.0, 0.2, 0.1)
    with col_tune2:
        max_partner_loss = st.slider("Maximum Loss Tolerated for Partner (pts/wk)", -4.0, 0.0, -1.5, 0.25)

# ----------------------------------------------------
# 2. Optimization & Evaluation Run
# ----------------------------------------------------
st.markdown("---")
run_scan = st.button("🚀 Find Optimal Trades", type="primary", use_container_width=True)

if run_scan:
    with st.spinner("Executing combinatorial knapsack optimization..."):
        other_teams = [t for t in teams if t["owner"] != my_owner]
        
        if selected_partner_option == "League-Wide (Scan All Teams)":
            target_opponents = other_teams
            max_per_team = 2
        else:
            target_opponents = [t for t in other_teams if t["owner"] == selected_partner_option]
            max_per_team = 15

        all_trades = []
        for opponent in target_opponents:
            trades = find_2team_trades(my_team, opponent, anchor_id=anchor_id)
            all_trades.extend(trades[:max_per_team])

        all_trades.sort(key=lambda x: (x["delta_my_team"], x["net_vorp_you"]), reverse=True)

    if not all_trades:
        st.warning("No trades met your optimization criteria. Try loosening the tolerance sliders.")
    else:
        st.subheader(f"Proposed Trade Recommendations ({len(all_trades)} identified)")
        
        # Display each trade in a card layout
        for i, tr in enumerate(all_trades[:15], 1):
            with st.container(border=True):
                col_info, col_metrics = st.columns([3, 1])
                
                with col_info:
                    st.markdown(f"### **Trade #{i} — Partner: `{tr['partner']}`**")
                    st.markdown(f"**Classification:** `{tr['grade']}` | **Structure:** `{tr['type']}`")
                    st.markdown(f"📤 **You Give:** {tr['gives']}")
                    st.markdown(f"📥 **You Receive:** {tr['receives']}")
                
                with col_metrics:
                    delta_color = "normal" if tr["delta_my_team"] >= 0 else "inverse"
                    st.metric("Your Lineup Δ", f"+{tr['delta_my_team']:.2f} pts/wk")
                    st.metric("Partner Lineup Δ", f"{tr['delta_partner']:.2f} pts/wk")
                    st.caption(f"Context VORP: +{tr['net_vorp_you']:.1f}")