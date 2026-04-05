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

    Uses get_all_values() instead of get_all_records() to avoid the
    GSpreadException raised when the header row contains empty/duplicate columns.
    Columns with empty headers are silently dropped.
    """
    ws = _get_worksheet(spreadsheet_id, sheet_name)
    all_values = _retry(ws.get_all_values)
    if not all_values or len(all_values) < 1:
        return pd.DataFrame()

    headers = all_values[0]
    rows    = all_values[1:]

    # Pad every row to header length so DataFrame constructor doesn't complain
    padded = [r + [""] * max(0, len(headers) - len(r)) for r in rows]
    df = pd.DataFrame(padded, columns=headers)

    # Drop columns whose header is empty or whitespace-only
    df = df[[h for h in df.columns if str(h).strip()]]
    return df


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


# ── Append helpers ────────────────────────────────────────────────────────────

def _clean_value(v) -> str:
    """Convert any value (incl. pandas NA / numpy NaN) to a clean string."""
    try:
        import pandas as pd
        if pd.isna(v):
            return ""
    except (TypeError, ImportError):
        pass
    if v is None:
        return ""
    return str(v)


def append_row_to_sheet(
    spreadsheet_id: str,
    sheet_name: str,
    row_data: dict,
) -> int:
    """
    Append a new data row at the bottom of the sheet (never to the right).
    Returns the 0-based DataFrame index of the new row.

    Uses the Sheets API's native append endpoint (ws.append_rows) which is
    atomic — the server determines the insertion row, so concurrent calls from
    a tight upload loop cannot overwrite each other (no read-then-write race).

    insert_data_option="INSERT_ROWS" prevents any risk of sideways writes by
    forcing the API to always open a new row rather than fill trailing cells.
    """
    import re as _re

    ws      = _get_worksheet(spreadsheet_id, sheet_name)
    headers = _get_headers(spreadsheet_id, sheet_name)
    row     = [_clean_value(row_data.get(h, "")) for h in headers]

    # append_rows returns the API response; updatedRange tells us the exact row.
    result = _retry(
        ws.append_rows,
        [row],
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
        table_range="A1",
    )

    # updatedRange looks like "SheetName!A5:G5" — extract the start row number.
    updated_range = result.get("updates", {}).get("updatedRange", "")
    m = _re.search(r":?[A-Z]+(\d+)", updated_range)
    written_row = int(m.group(1)) if m else None

    if written_row is None:
        # Fallback: count rows after the write (rare, but safe)
        written_row = len(_retry(ws.get_all_values))

    sheet_idx = written_row - 2               # 0-based DataFrame index (row 1 = header)
    load_sheet_df.clear()
    return sheet_idx


# ── append_annotation_row — horse.py multi-label feature ─────────────────────

def append_annotation_row(
    spreadsheet_id: str,
    sheet_name: str,
    row_data: dict,
    override_label: str = None,
) -> int:
    """
    Append a copy of row_data as a new row.
    If override_label is provided, it replaces the 'label' key — used by
    horse.py when the annotator adds extra labels for the same image.
    """
    data = dict(row_data)   # don't mutate the original
    if override_label is not None:
        data["label"]        = override_label
        data["status"]       = "done"
        data["annotated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return append_row_to_sheet(spreadsheet_id, sheet_name, data)


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
