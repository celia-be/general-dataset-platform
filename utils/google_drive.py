"""
Google Drive utility — load images on demand from a private Drive folder.
Uses a GCP service account configured in .streamlit/secrets.toml.
"""

import io
import streamlit as st
from PIL import Image
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ── Service (cached for the lifetime of the Streamlit process) ──────────────

@st.cache_resource
def _get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── Image loading (cached 10 min per file_id) ────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def load_image_from_drive(file_id: str) -> Image.Image:
    """Fetch any image file from Google Drive and return as a RGB PIL Image."""
    service = _get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return Image.open(buf).convert("RGB")


# ── Helpers ──────────────────────────────────────────────────────────────────

def drive_view_url(file_id: str) -> str:
    """Return a browser-openable URL for a Drive file (PDF, image, etc.)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def resize_for_display(img: Image.Image, max_px: int = 500) -> Image.Image:
    """Return a copy of img scaled so the longest edge ≤ max_px."""
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.LANCZOS)
    return out
