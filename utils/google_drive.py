"""
Google Drive utility — load images on demand + upload anonymised images.
Uses a GCP service account configured in .streamlit/secrets.toml.
"""

import io
import streamlit as st
from PIL import Image
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import json


# ── Read-only service (image loading) ────────────────────────────────────────

@st.cache_resource
def _get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── Read-write service (uploads) ─────────────────────────────────────────────

@st.cache_resource
def _get_drive_rw_service():
    """Separate service with full drive scope for file uploads."""
    creds = service_account.Credentials.from_service_account_info(
        json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=["https://www.googleapis.com/auth/drive"],
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


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_pil_image_to_drive(img: Image.Image, filename: str, folder_id: str) -> str:
    """
    Upload a PIL Image as PNG to a Google Drive folder.
    Returns the file_id of the newly created file.
    """
    service = _get_drive_rw_service()

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    file_metadata = {
        "name":    filename,
        "parents": [folder_id],
    }
    media = MediaIoBaseUpload(buf, mimetype="image/png", resumable=False)

    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()

    return result["id"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def drive_view_url(file_id: str) -> str:
    """Return a browser-openable URL for a Drive file (PDF, image, etc.)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def resize_for_display(img: Image.Image, max_px: int = 500) -> Image.Image:
    """Return a copy of img scaled so the longest edge <= max_px."""
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.LANCZOS)
    return out
