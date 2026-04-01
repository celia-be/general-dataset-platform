"""
Google Sheets utility — read annotation progress and write results.

Each module has its own Sheet (configured in secrets.toml).
Rows start with status="pending" and are updated to status="done" after annotation.
This gives automatic resume-from-where-you-left-off across sessions.

Rate-limit strategy
-------------------
• load_sheet_df  : cached 30 s  (busted explicitly after every write)
• _get_headers   : cached 10 min (column names almost never change)
• All API calls  : wrapped in _retry() — exponential backoff on 429 / 5xx
"""

import time
import streamlit as st
import pandas as pd
import gspread
from google.oauth2 import service_account
from datetime import datetime
from typing import Optional
import json

# ── Client (cached for the lifetime of the Streamlit process) ────────────────

@st.cache_resource
def _get_gspread_client():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds)


# ── Retry helper ─────────────────────────────────────────────────────────────

def _retry(fn, *args, retries: int = 5, base_delay: float = 2.0, **kwargs):
    """
    Call fn(*args, **kwargs), retrying on quota / server errors.
    Delays: 2 s, 4 s, 8 s, 16 s, 32 s  (exponential backoff).
    Raises on the last attempt or for non-retryable errors.
    """
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            code = exc.response.status_code if hasattr(exc, "response") else 429
            if code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = base_delay * (2 ** attempt)
                time.sleep(wait)
            else:
                raise


# ── Worksheet access ─────────────────────────────────────────────────────────

def _get_worksheet(spreadsheet_id: str, sheet_name: str) -> gspread.Worksheet:
    client = _get_gspread_client()
    return client.open_by_key(spreadsheet_id).worksheet(sheet_name)


# ── Headers cache (long TTL — column names almost never change) ───────────────

@st.cache_data(ttl=600, show_spinner=False)
def _get_headers(spreadsheet_id: str, sheet_name: str) -> list:
    """Return the first row (column names). Cached 10 min."""
    ws = _get_worksheet(spreadsheet_id, sheet_name)
    return _retry(ws.row_values, 1)


# ── Read (TTL long enough to absorb reruns; busted after every write) ─────────

@st.cache_data(ttl=30, show_spinner=False)
def load_sheet_df(spreadsheet_id: str, sheet_name: str) -> pd.DataFrame:
    """
    Load the full sheet as a DataFrame.
    Cached for 30 s — explicitly cleared after every save_annotation() call,
    so the annotator always sees up-to-date progress immediately after saving.
    """
    ws = _get_worksheet(spreadsheet_id, sheet_name)
    records = _retry(ws.get_all_records, default_blank="")
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
    df_index 0 → Sheet row 2  (row 1 is the header).
    Uses the cached headers to avoid an extra read request on every save.
    """
    if mark_done:
        updates["status"] = "done"
        updates["annotated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws      = _get_worksheet(spreadsheet_id, sheet_name)
    headers = _get_headers(spreadsheet_id, sheet_name)   # ← cached, no extra quota hit
    sheet_row = df_index + 2                             # row 1 = header, data starts row 2

    cells = []
    for col_name, value in updates.items():
        if col_name in headers:
            col_idx = headers.index(col_name) + 1        # 1-based
            cells.append(gspread.Cell(sheet_row, col_idx, str(value) if value is not None else ""))

    if cells:
        _retry(ws.update_cells, cells, value_input_option="USER_ENTERED")

    # Bust the read cache so the next load_sheet_df() sees the updated row
    load_sheet_df.clear()


# ── Append (extra labels) ────────────────────────────────────────────────────

def append_annotation_row(
    spreadsheet_id: str,
    sheet_name: str,
    source_row: dict,   # full row data from df (as dict)
    updates: dict,      # fields to override (must include "label")
) -> None:
    """
    Append a new row that duplicates source_row with overridden fields.
    Used when an image has multiple anomaly labels.
    Always marks the new row status=done.
    """
    updates["status"] = "done"
    updates["annotated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws      = _get_worksheet(spreadsheet_id, sheet_name)
    headers = _get_headers(spreadsheet_id, sheet_name)

    merged  = {**source_row, **updates}
    new_row = [str(merged.get(h, "")) for h in headers]

    _retry(ws.append_row, new_row, value_input_option="USER_ENTERED")
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


def progress_stats(df: pd.DataFrame) -> tuple:
    """Return (done_count, total_count)."""
    if df.empty or "status" not in df.columns:
        return 0, 0
    done = int((df["status"].str.lower() == "done").sum())
    return done, len(df)
