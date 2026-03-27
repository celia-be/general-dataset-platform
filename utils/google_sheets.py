"""
Google Sheets utility — read annotation progress and write results.

Each module has its own Sheet (configured in secrets.toml).
Rows start with status="pending" and are updated to status="done" after annotation.
This gives automatic resume-from-where-you-left-off across sessions.
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from datetime import datetime
from typing import Optional

# ── Client (cached for the lifetime of the Streamlit process) ────────────────

@st.cache_resource
def _get_gspread_client():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds)


# ── Worksheet access ─────────────────────────────────────────────────────────

def _get_worksheet(spreadsheet_id: str, sheet_name: str) -> gspread.Worksheet:
    client = _get_gspread_client()
    return client.open_by_key(spreadsheet_id).worksheet(sheet_name)


# ── Read (short TTL so progress is always fresh after a save) ────────────────

@st.cache_data(ttl=8, show_spinner=False)
def load_sheet_df(spreadsheet_id: str, sheet_name: str) -> pd.DataFrame:
    """Load the full sheet as a DataFrame. Cached for 8 s to absorb reruns."""
    ws = _get_worksheet(spreadsheet_id, sheet_name)
    records = ws.get_all_records(default_blank="")
    return pd.DataFrame(records) if records else pd.DataFrame()


# ── Write ────────────────────────────────────────────────────────────────────

def save_annotation(
    spreadsheet_id: str,
    sheet_name: str,
    df_index: int,           # 0-based DataFrame row index
    updates: dict,           # {column_name: value, ...}
    mark_done: bool = True,
) -> None:
    """
    Write annotation fields + status/timestamp for one row.
    df_index 0 → Sheet row 2 (row 1 is the header).
    """
    if mark_done:
        updates["status"] = "done"
        updates["annotated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws = _get_worksheet(spreadsheet_id, sheet_name)
    headers = ws.row_values(1)           # column names
    sheet_row = df_index + 2            # gspread rows are 1-based; row 1 = header

    cells = []
    for col_name, value in updates.items():
        if col_name in headers:
            col_idx = headers.index(col_name) + 1   # 1-based
            cells.append(gspread.Cell(sheet_row, col_idx, str(value) if value is not None else ""))

    if cells:
        ws.update_cells(cells, value_input_option="USER_ENTERED")

    # Bust the cache so the next load_sheet_df() sees the update
    load_sheet_df.clear()


# ── Progress helpers ─────────────────────────────────────────────────────────

def get_current_index(df: pd.DataFrame) -> Optional[int]:
    """
    Return the DataFrame index of the first row where status != 'done'.
    Returns None if everything is annotated.
    """
    if df.empty or "status" not in df.columns:
        return None
    pending = df[df["status"].str.lower() != "done"]
    return int(pending.index[0]) if not pending.empty else None


def progress_stats(df: pd.DataFrame) -> tuple[int, int]:
    """Return (done_count, total_count)."""
    if df.empty or "status" not in df.columns:
        return 0, 0
    done = int((df["status"].str.lower() == "done").sum())
    return done, len(df)
