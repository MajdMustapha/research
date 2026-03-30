"""
Streamlit dashboard for the Polymarket Weather Bot.
Displays P&L, city scores, open positions, and agent state.

Run: streamlit run dashboard/app.py --server.port 8501
"""
from __future__ import annotations

import json
import os
import sqlite3

import pandas as pd
import streamlit as st

STATE_PATH = os.environ.get("AGENT_STATE_PATH", "data/agent_state.json")
CROWDING_DB = "data/crowding.db"


def load_agent_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_crowding_data() -> pd.DataFrame:
    if not os.path.exists(CROWDING_DB):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(CROWDING_DB)
        df = pd.read_sql_query(
            "SELECT * FROM crowding_history ORDER BY ranked_at DESC", conn
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def main() -> None:
    st.set_page_config(
        page_title="Weather Bot Dashboard",
        layout="wide",
    )
    st.title("Polymarket Weather Bot")

    state = load_agent_state()

    # ── Sidebar ──────────────────────────────────────────────────
    st.sidebar.header("Agent Status")
    st.sidebar.write(f"Last updated: {state.get('last_updated', 'N/A')}")
    st.sidebar.write(f"Live mode: {state.get('live_mode', False)}")

    balance = state.get("last_balance_check", {})
    st.sidebar.metric("USDC Balance", f"${balance.get('usdc', 0):.2f}")

    open_count = len(state.get("open_positions", []))
    paper_count = len(state.get("paper_positions", []))
    st.sidebar.metric("Open Positions", open_count)
    st.sidebar.metric("Paper Positions", paper_count)

    # ── Tabs ─────────────────────────────────────────────────────
    tab_pnl, tab_cities, tab_positions, tab_state = st.tabs(
        ["P&L", "City Rankings", "Positions", "Agent State"]
    )

    with tab_pnl:
        st.subheader("Daily P&L")
        daily_pnl = state.get("daily_realised_pnl", {})
        rebates = state.get("maker_rebate_income", {})

        if daily_pnl or rebates:
            all_dates = sorted(set(list(daily_pnl.keys()) + list(rebates.keys())))
            pnl_data = []
            for d in all_dates:
                pnl_data.append({
                    "date": d,
                    "Directional P&L": daily_pnl.get(d, 0),
                    "Maker Rebates": rebates.get(d, 0),
                })
            df_pnl = pd.DataFrame(pnl_data)
            if not df_pnl.empty:
                df_pnl = df_pnl.set_index("date")
                st.line_chart(df_pnl)

                total_dir = sum(daily_pnl.values())
                total_reb = sum(rebates.values())
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Directional", f"${total_dir:+.2f}")
                col2.metric("Total Rebates", f"${total_reb:+.2f}")
                col3.metric("Combined", f"${total_dir + total_reb:+.2f}")
        else:
            st.info("No P&L data yet. Run backtest or live mode first.")

    with tab_cities:
        st.subheader("City Crowding Rankings")
        df_crowd = load_crowding_data()
        if not df_crowd.empty:
            latest = df_crowd.groupby("city").first().reset_index()
            latest = latest.sort_values("opportunity_score", ascending=False)
            st.dataframe(
                latest[["city", "opportunity_score", "entry_window_minutes",
                         "resolution_station", "trend", "markets_analysed"]],
                use_container_width=True,
            )
        else:
            st.info("No crowding data. Run: python main.py crowd-rank")

        city_configs = state.get("city_configs", {})
        if city_configs:
            st.subheader("Active City Configs")
            st.json(city_configs)

    with tab_positions:
        st.subheader("Open Positions")
        positions = state.get("open_positions", [])
        if positions:
            st.dataframe(pd.DataFrame(positions), use_container_width=True)
        else:
            st.info("No open positions.")

        st.subheader("Paper Positions")
        paper = state.get("paper_positions", [])
        if paper:
            st.dataframe(pd.DataFrame(paper[-50:]), use_container_width=True)
        else:
            st.info("No paper positions.")

    with tab_state:
        st.subheader("City Health")
        health = state.get("city_health", {})
        if health:
            st.json(health)

        st.subheader("Full Agent State")
        st.json(state)


if __name__ == "__main__":
    main()
