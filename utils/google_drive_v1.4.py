"""
Google Drive utility — load images on demand from Drive.
Google Cloud Storage utility — upload anonymised images (service accounts
  have no Drive storage quota; GCS is the correct target for uploads).

Uses a GCP service account configured in .streamlit/secrets.toml.

secrets.toml additions for GCS uploads:
  [gcs]
  bucket_name = "your-bucket-name"   # e.g. "delara-cheval-upload"
"""

import io
import streamlit as st
from PIL import Image
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import json

# google-cloud-storage is only required for the cheval_upload module.
# Imported lazily inside _get_gcs_client() so that horse/pets/data
# keep working even if the package is not installed.


# ── Drive read-only service (image loading for existing modules) ──────────────

@st.cache_resource
def _get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── GCS client (uploads — service accounts have full quota here) ──────────────

@st.cache_resource
def _get_gcs_client():
    from google.cloud import storage as gcs_lib   # lazy import — only for cheval_upload
    creds = service_account.Credentials.from_service_account_info(
        json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return gcs_lib.Client(credentials=creds, project=creds.project_id)

def _make_gcs_client():
    """
    Create a FRESH GCS client for each upload.

    The client is intentionally NOT cached here: reusing a cached client
    across multiple uploads in the same Streamlit script run leaves the
    internal HTTP connection pool in a dirty state after the first upload,
    causing all subsequent uploads to silently fail or use stale connections.
    A fresh client costs only a few milliseconds and guarantees a clean
    connection for every request.
    """
    from google.cloud import storage as gcs_lib
    creds = _get_gcs_client()
    return gcs_lib.Client(credentials=creds, project=creds.project_id)

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


# ── GCS Upload ────────────────────────────────────────────────────────────────

# def upload_pil_image_to_gcs(img: Image.Image, filename: str, bucket_name: str) -> str:
#     """
#     Upload a PIL Image as PNG to a GCS bucket.
#     Returns the full GCS URI: gs://bucket_name/filename
#     Service accounts have full storage quota in GCS — no Drive quota issues.
#     """
#     client = _get_gcs_client()
#     bucket = client.bucket(bucket_name)
#     blob   = bucket.blob(filename)

#     buf = io.BytesIO()
#     img.save(buf, format="PNG")
#     buf.seek(0)

#     blob.upload_from_file(buf, content_type="image/png")
#     return f"gs://{bucket_name}/{filename}"

# ── GCS Upload ────────────────────────────────────────────────────────────────

def upload_pil_image_to_gcs(img: Image.Image, filename: str, bucket_name: str) -> str:
    """
    Upload a PIL Image as PNG to GCS. Returns gs://bucket_name/filename.

    Creates a fresh GCS client on every call to avoid connection-pool
    state issues when called multiple times in the same script run.
    Uses upload_from_string() so stream position is never a concern.
    """
    client = _make_gcs_client()          # fresh client — no cached state
    bucket = client.bucket(bucket_name)
    blob   = bucket.blob(filename)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()         # read all bytes before any upload call

    blob.upload_from_string(image_bytes, content_type="image/png")
    return f"gs://{bucket_name}/{filename}"

# ── Helpers ───────────────────────────────────────────────────────────────────

def drive_view_url(file_id: str) -> str:
    """Return a browser-openable URL for a Drive file (PDF, image, etc.)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def resize_for_display(img: Image.Image, max_px: int = 500) -> Image.Image:
    """Return a copy of img scaled so the longest edge <= max_px."""
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.LANCZOS)
    return out
