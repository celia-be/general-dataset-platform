"""
Cheval Upload — Horse X-Ray Upload & Annotation module (Olivier).

Workflow:
  1. Upload an X-ray image (PNG/JPEG)
  2. Preview & adjust anonymisation crop (top + bottom bands removed)
  3. Upload anonymised image to Google Drive folder
  4. Draw bounding boxes + enter a free-text label
  5. Save → writes image_id, image_name, label, bbox to Google Sheets

Expected Google Sheet columns:
  image_id | image_name | label | bbox | status | uploaded_at | annotated_at

secrets.toml additions needed:
  [sheets]
  cheval_upload_sheet_id   = "..."
  cheval_upload_sheet_name = "..."

  [drive]
  cheval_upload_folder_id  = "..."   # ID of "Data Images Chevaux - Olivier"

  [passwords]
  cheval_upload = "..."
"""

import io
import json
from datetime import datetime

import streamlit as st
from PIL import Image, ImageDraw

from utils.google_drive import upload_pil_image_to_drive
from utils.google_sheets import append_row_to_sheet, save_annotation

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
    HAS_COORDS = True
except ImportError:
    HAS_COORDS = False

# ── Constants ─────────────────────────────────────────────────────────────────

BBOX_SIZE        = 40   # half-side of the bounding box in pixels (on resized image)
DEFAULT_CROP_PCT = 8    # default % to crop from top and bottom


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
        "cheval_step", "cheval_image_bytes", "cheval_file_name",
        "cheval_file_id", "cheval_sheet_idx", "cheval_clicks",
    ]:
        st.session_state.pop(key, None)


def _anonymize(img: Image.Image, top_pct: float, bot_pct: float) -> Image.Image:
    """Crop top_pct% from top and bot_pct% from bottom."""
    w, h = img.size
    top_px = int(h * top_pct / 100)
    bot_px = int(h * bot_pct / 100)
    bot_px = max(bot_px, 1)  # always crop at least 1px so crop is valid
    return img.crop((0, top_px, w, h - bot_px))


def _preview_overlay(img: Image.Image, top_pct: float, bot_pct: float) -> Image.Image:
    """Draw red bands on a copy of the image to show what will be removed."""
    out = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = out.size
    top_px = int(h * top_pct / 100)
    bot_px = int(h * bot_pct / 100)
    draw.rectangle([(0, 0), (w, top_px)], fill=(200, 0, 0, 160))
    draw.rectangle([(0, h - bot_px), (w, h)], fill=(200, 0, 0, 160))
    return Image.alpha_composite(out, overlay).convert("RGB")


def _draw_boxes(img: Image.Image, clicks: list, box_size: int = BBOX_SIZE) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for pt in clicks:
        x, y = pt["x"], pt["y"]
        draw.rectangle(
            [(x - box_size, y - box_size), (x + box_size, y + box_size)],
            outline="red", width=3,
        )
    return out


# ── Step 1 : Upload + crop preview ───────────────────────────────────────────

def _show_upload(sheet_id: str, sheet_name: str, folder_id: str):
    st.markdown("### Étape 1 — Chargement de l'image")
    st.caption("Les bandes en haut et en bas seront supprimées pour retirer les informations médicales.")

    uploaded = st.file_uploader(
        "Sélectionner une radiographie (PNG ou JPEG)",
        type=["png", "jpg", "jpeg"],
        key="cheval_uploader",
    )

    if not uploaded:
        return

    img = Image.open(uploaded).convert("RGB")

    st.markdown("---")
    st.markdown("### Étape 2 — Ajuster l'anonymisation")

    col_sl, _ = st.columns([1, 2])
    with col_sl:
        top_pct = st.slider("Rogner en haut (%)", 0, 30, DEFAULT_CROP_PCT, key="cheval_top_pct")
        bot_pct = st.slider("Rogner en bas (%)",  0, 30, DEFAULT_CROP_PCT, key="cheval_bot_pct")

    col_orig, col_result = st.columns(2)

    with col_orig:
        st.caption("📷 Original — zone rouge = supprimée")
        preview = _preview_overlay(img, top_pct, bot_pct)
        preview.thumbnail((460, 460), Image.LANCZOS)
        st.image(preview, use_container_width=True)

    with col_result:
        st.caption("✅ Résultat après anonymisation")
        anon_thumb = _anonymize(img, top_pct, bot_pct)
        anon_thumb.thumbnail((460, 460), Image.LANCZOS)
        st.image(anon_thumb, use_container_width=True)

    st.markdown("")
    if st.button("☁️ Uploader & Annoter →", key="cheval_upload_btn"):
        # Anonymize at full resolution
        anon_full = _anonymize(img, top_pct, bot_pct)

        # Build unique filename
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        orig_stem = uploaded.name.rsplit(".", 1)[0]
        filename  = f"{orig_stem}_anon_{ts}.png"

        with st.spinner("Upload vers Google Drive…"):
            file_id = upload_pil_image_to_drive(anon_full, filename, folder_id)

        with st.spinner("Enregistrement dans Google Sheets…"):
            sheet_idx = append_row_to_sheet(
                sheet_id, sheet_name,
                {
                    "image_id":    file_id,
                    "image_name":  filename,
                    "label":       "",
                    "bbox":        "",
                    "status":      "pending",
                    "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "annotated_at": "",
                },
            )

        # Serialize anonymized image to bytes for session state
        buf = io.BytesIO()
        anon_full.save(buf, format="PNG")

        st.session_state.cheval_image_bytes = buf.getvalue()
        st.session_state.cheval_file_id     = file_id
        st.session_state.cheval_file_name   = filename
        st.session_state.cheval_sheet_idx   = sheet_idx
        st.session_state.cheval_clicks      = []
        st.session_state.cheval_step        = "annotate"
        st.rerun()


# ── Step 2 : Annotation ───────────────────────────────────────────────────────

def _show_annotate(sheet_id: str, sheet_name: str):
    file_name = st.session_state.get("cheval_file_name", "image")
    img       = Image.open(io.BytesIO(st.session_state.cheval_image_bytes))
    img_display = img.copy()
    img_display.thumbnail((500, 500), Image.LANCZOS)

    st.markdown(f"### Étape 3 — Annoter : `{file_name}`")
    st.caption("Cliquer sur l'image pour placer des bounding boxes, puis saisir le label.")

    col_click, col_preview, col_form = st.columns([1.3, 1.3, 1.1])

    # ── Column 1 : click to place boxes ──────────────────────────────────────
    with col_click:
        st.caption("🖱️ Cliquer pour placer des boxes")
        if HAS_COORDS:
            coords = streamlit_image_coordinates(img_display, key="cheval_click_img")
            if coords:
                new_pt = {"x": coords["x"], "y": coords["y"]}
                clicks = st.session_state.cheval_clicks
                if not clicks or clicks[-1] != new_pt:
                    clicks.append(new_pt)
        else:
            st.image(img_display, use_container_width=True)
            st.info("Installer `streamlit-image-coordinates` pour activer le placement de boxes.")

        if st.button("✏️ Effacer les boxes", key="cheval_clear"):
            st.session_state.cheval_clicks = []
            st.rerun()

    # ── Column 2 : bbox preview ───────────────────────────────────────────────
    with col_preview:
        st.caption("👁️ Aperçu")
        clicks = st.session_state.cheval_clicks
        if clicks:
            preview = _draw_boxes(img_display, clicks)
            st.image(preview, use_container_width=True)
            st.markdown("**Coordonnées :**")
            for i, pt in enumerate(clicks):
                st.markdown(f"• Box {i+1} : `x={pt['x']}` `y={pt['y']}` `taille={BBOX_SIZE*2}px`")
        else:
            st.image(img_display, use_container_width=True)
            st.caption("Aucune box pour l'instant.")

    # ── Column 3 : form ───────────────────────────────────────────────────────
    with col_form:
        st.markdown("**Annotation**")
        st.markdown("---")

        label = st.text_input(
            "✏️ Label",
            placeholder="Ex : fracture, périostite, arthrose…",
            key="cheval_label_input",
        )

        st.markdown("")

        if st.button("💾 Sauvegarder & Upload suivant →", use_container_width=True, key="cheval_save"):
            bbox_list = [
                {
                    "x": pt["x"], "y": pt["y"],
                    "width": BBOX_SIZE * 2, "height": BBOX_SIZE * 2,
                }
                for pt in st.session_state.cheval_clicks
            ]
            save_annotation(
                sheet_id, sheet_name,
                st.session_state.cheval_sheet_idx,
                {
                    "label": label,
                    "bbox":  json.dumps(bbox_list) if bbox_list else "",
                },
            )
            _clear_state()
            st.rerun()

        if st.button("⏭️ Passer (sans annotation)", use_container_width=True, key="cheval_skip"):
            save_annotation(
                sheet_id, sheet_name,
                st.session_state.cheval_sheet_idx,
                {"label": "", "bbox": ""},
            )
            _clear_state()
            st.rerun()


# ── Entry point ───────────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id   = st.secrets["sheets"]["cheval_upload_sheet_id"]
    sheet_name = st.secrets["sheets"]["cheval_upload_sheet_name"]
    folder_id  = st.secrets["drive"]["cheval_upload_folder_id"]

    if "cheval_step"  not in st.session_state:
        st.session_state.cheval_step  = "upload"
    if "cheval_clicks" not in st.session_state:
        st.session_state.cheval_clicks = []

    if (
        st.session_state.cheval_step == "annotate"
        and "cheval_image_bytes" in st.session_state
    ):
        _show_annotate(sheet_id, sheet_name)
    else:
        st.session_state.cheval_step = "upload"
        _show_upload(sheet_id, sheet_name, folder_id)
