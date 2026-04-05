"""
Cheval Upload — Horse X-Ray Upload & Annotation module.

Workflow:
  1. Upload one or several X-ray images (PNG/JPEG) in one go.
  2. All images are anonymised automatically in the backend (a band is cropped
     from the top and bottom, sized as ANON_CROP_RATIO × min(width, height) of
     each image, so it adapts to any resolution and aspect ratio).
  3. Each anonymised image is uploaded to Google Cloud Storage and registered
     as a "pending" row in Google Sheets.
  4. The user annotates images one by one from the queue: draw bounding boxes
     and enter a free-text label.
  5. Save → updates the corresponding Sheets row to "done".

Expected Google Sheet columns:
  image_id | image_name | label | bbox | status | uploaded_at | annotated_at

secrets.toml additions needed:
  [sheets]
  cheval_upload_sheet_id   = "..."
  cheval_upload_sheet_name = "..."

  [gcs]
  bucket_name = "delara-cheval-upload"

  [passwords]
  cheval_upload = "..."
"""

import io
import json
from datetime import datetime

import streamlit as st
from PIL import Image, ImageDraw

from utils.google_drive import upload_pil_image_to_gcs
from utils.google_sheets import append_row_to_sheet, save_annotation

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
    HAS_COORDS = True
except ImportError:
    HAS_COORDS = False

# ── Constants ─────────────────────────────────────────────────────────────────

BBOX_SIZE = 40  # half-side of bounding box in pixels (on the resized display image)

# Fraction of min(width, height) cropped from EACH of the top and bottom edges.
# e.g. 0.08 → 8 % of the shorter dimension removed per edge.
# Using the shorter dimension means the strip scales naturally regardless of
# whether the image is portrait, landscape, or square.
ANON_CROP_RATIO = 0.08


# ── Helpers ───────────────────────────────────────────────────────────────────

def _header():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Portal"):
            _clear_state()
            st.session_state.module = None
            st.session_state.auth.pop("cheval_upload", None)
            st.rerun()
    with col_title:
        st.markdown("## 🐴 Images Chevaux — Upload & Annotation")


def _clear_state():
    for key in [
        "cheval_step", "cheval_queue", "cheval_queue_pos", "cheval_clicks",
    ]:
        st.session_state.pop(key, None)


def _anonymize(img: Image.Image) -> Image.Image:
    """
    Remove a band from the top and bottom of the image to strip out any
    patient / medical metadata that may appear there.

    The band height is: max(1, int(min(width, height) * ANON_CROP_RATIO))
    This ensures the crop scales with the image content (not a fixed pixel
    count) and is capped to at most 25 % of the total height so no diagnostic
    area is accidentally removed.
    """
    w, h = img.size
    crop_px = max(1, int(min(w, h) * ANON_CROP_RATIO))
    crop_px = min(crop_px, h // 4)          # never remove more than 25 % top + bottom
    return img.crop((0, crop_px, w, h - crop_px))


def _draw_boxes(img: Image.Image, clicks: list, box_size: int = BBOX_SIZE) -> Image.Image:
    out  = img.copy()
    draw = ImageDraw.Draw(out)
    for pt in clicks:
        x, y = pt["x"], pt["y"]
        draw.rectangle(
            [(x - box_size, y - box_size), (x + box_size, y + box_size)],
            outline="red", width=3,
        )
    return out


# ── Step 1 : Upload ───────────────────────────────────────────────────────────

def _show_upload(sheet_id: str, sheet_name: str, bucket_name: str):
    st.markdown("### Étape 1 — Chargement des images")
    st.caption(
        "Sélectionner une ou plusieurs radiographies. "
        "L'anonymisation (suppression automatique des bandes haut/bas) "
        "est appliquée en arrière-plan avant tout stockage."
    )

    uploaded_files = st.file_uploader(
        "Sélectionner des radiographies (PNG ou JPEG)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="cheval_uploader",
    )

    if not uploaded_files:
        return

    n = len(uploaded_files)
    plural = "s" if n > 1 else ""
    st.info(f"**{n} image{plural}** sélectionnée{plural}.")

    if st.button(f"☁️ Uploader {n} image{plural} et annoter →", key="cheval_upload_btn"):
        queue        = []
        progress_bar = st.progress(0)
        status_text  = st.empty()

        for i, uploaded in enumerate(uploaded_files):
            status_text.text(f"Traitement {i + 1}/{n} : {uploaded.name}…")

            img       = Image.open(uploaded).convert("RGB")
            anon_full = _anonymize(img)

            # Unique filename: original stem + timestamp + index to avoid
            # collisions when several files are processed in the same second.
            ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
            orig_stem = uploaded.name.rsplit(".", 1)[0]
            filename  = f"{orig_stem}_anon_{ts}_{i:03d}.png"

            with st.spinner(f"Upload GCS : {filename}…"):
                file_id = upload_pil_image_to_gcs(anon_full, filename, bucket_name)

            with st.spinner("Enregistrement dans Google Sheets…"):
                sheet_idx = append_row_to_sheet(
                    sheet_id, sheet_name,
                    {
                        "image_id":     file_id,
                        "image_name":   filename,
                        "label":        "",
                        "bbox":         "",
                        "status":       "pending",
                        "uploaded_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "annotated_at": "",
                    },
                )

            # Serialise the anonymised image into bytes for session state.
            buf = io.BytesIO()
            anon_full.save(buf, format="PNG")

            queue.append({
                "image_bytes": buf.getvalue(),
                "file_id":     file_id,
                "file_name":   filename,
                "sheet_idx":   sheet_idx,
            })

            progress_bar.progress((i + 1) / n)

        status_text.text(f"✅ {n} image{plural} uploadée{plural} avec succès !")

        st.session_state.cheval_queue     = queue
        st.session_state.cheval_queue_pos = 0
        st.session_state.cheval_clicks    = []
        st.session_state.cheval_step      = "annotate"
        st.rerun()


# ── Step 2 : Annotation (queue) ───────────────────────────────────────────────

def _show_annotate(sheet_id: str, sheet_name: str):
    queue = st.session_state.cheval_queue
    pos   = st.session_state.cheval_queue_pos
    total = len(queue)

    # All images annotated
    if pos >= total:
        st.success(f"✅ Toutes les {total} images ont été annotées !")
        if st.button("⬆️ Uploader d'autres images"):
            _clear_state()
            st.rerun()
        return

    current   = queue[pos]
    file_name = current["file_name"]

    img         = Image.open(io.BytesIO(current["image_bytes"]))
    img_display = img.copy()
    img_display.thumbnail((500, 500), Image.LANCZOS)

    # ── Progress bar + title ─────────────────────────────────────────────────
    st.progress(pos / total)
    st.markdown(f"### Étape 2 — Annotation : image **{pos + 1} / {total}**")
    st.caption(f"`{file_name}`")

    col_click, col_preview, col_form = st.columns([1.3, 1.3, 1.1])

    # ── Column 1 : click to place boxes ──────────────────────────────────────
    with col_click:
        st.caption("🖱️ Cliquer pour placer des boxes")
        if HAS_COORDS:
            coords = streamlit_image_coordinates(
                img_display, key=f"cheval_click_img_{pos}"
            )
            if coords:
                new_pt = {"x": coords["x"], "y": coords["y"]}
                clicks = st.session_state.cheval_clicks
                if not clicks or clicks[-1] != new_pt:
                    clicks.append(new_pt)
        else:
            st.image(img_display, width="stretch")
            st.info("Installer `streamlit-image-coordinates` pour activer le placement de boxes.")

        if st.button("✏️ Effacer les boxes", key=f"cheval_clear_{pos}"):
            st.session_state.cheval_clicks = []
            st.rerun()

    # ── Column 2 : bbox preview ───────────────────────────────────────────────
    with col_preview:
        st.caption("👁️ Aperçu")
        clicks = st.session_state.cheval_clicks
        if clicks:
            preview = _draw_boxes(img_display, clicks)
            st.image(preview, width="stretch")
            st.markdown("**Coordonnées :**")
            for i, pt in enumerate(clicks):
                st.markdown(
                    f"• Box {i + 1} : `x={pt['x']}` `y={pt['y']}` "
                    f"`taille={BBOX_SIZE * 2}px`"
                )
        else:
            st.image(img_display, width="stretch")
            st.caption("Aucune box pour l'instant.")

    # ── Column 3 : form ───────────────────────────────────────────────────────
    with col_form:
        st.markdown("**Annotation**")
        st.markdown("---")

        label = st.text_input(
            "✏️ Label",
            placeholder="Ex : fracture, périostite, arthrose…",
            key=f"cheval_label_input_{pos}",
        )

        st.markdown("")

        next_label = "Image suivante →" if pos < total - 1 else "Terminer ✅"

        if st.button(
            f"💾 Sauvegarder & {next_label}",
            use_container_width=True,
            key=f"cheval_save_{pos}",
        ):
            bbox_list = [
                {
                    "x": pt["x"], "y": pt["y"],
                    "width":  BBOX_SIZE * 2,
                    "height": BBOX_SIZE * 2,
                }
                for pt in st.session_state.cheval_clicks
            ]
            save_annotation(
                sheet_id, sheet_name,
                current["sheet_idx"],
                {
                    "label": label,
                    "bbox":  json.dumps(bbox_list) if bbox_list else "",
                },
            )
            st.session_state.cheval_queue_pos += 1
            st.session_state.cheval_clicks     = []
            st.rerun()

        if st.button(
            "⏭️ Passer (sans annotation)",
            use_container_width=True,
            key=f"cheval_skip_{pos}",
        ):
            save_annotation(
                sheet_id, sheet_name,
                current["sheet_idx"],
                {"label": "", "bbox": ""},
            )
            st.session_state.cheval_queue_pos += 1
            st.session_state.cheval_clicks     = []
            st.rerun()


# ── Entry point ───────────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id    = st.secrets["sheets"]["cheval_upload_sheet_id"]
    sheet_name  = st.secrets["sheets"]["cheval_upload_sheet_name"]
    bucket_name = st.secrets["gcs"]["bucket_name"]

    if "cheval_step"   not in st.session_state:
        st.session_state.cheval_step  = "upload"
    if "cheval_clicks" not in st.session_state:
        st.session_state.cheval_clicks = []

    if (
        st.session_state.cheval_step == "annotate"
        and "cheval_queue" in st.session_state
    ):
        _show_annotate(sheet_id, sheet_name)
    else:
        st.session_state.cheval_step = "upload"
        _show_upload(sheet_id, sheet_name, bucket_name)
