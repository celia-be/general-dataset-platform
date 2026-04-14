"""
Horse X-Ray Annotation module.

What the annotator does:
  1. View the X-ray image (with zoom) on the left
  2. Read the PDF report embedded in the centre panel
  3. Select Membre / Zone / Vue from dropdowns
  4. Edit the auto-generated manual_label if needed
  5. Add a free-text report_description (findings / notes from the report)
  6. Click "Save & Next" → writes all fields to Google Sheets

Progress is persisted in Google Sheets: on reload, the app resumes
from the first row where status != 'done'.

Expected Google Sheet columns:
  image_id | anonymized_image | report_id | anonymized_report |
  membre | body_part | view | label | custom_report |
  Consultation Date | status | annotated_at
"""

import io

import fitz  # pymupdf
from PIL import Image

import streamlit as st
from utils.google_drive import load_image_from_drive, resize_for_display, load_pdf_from_drive
from utils.google_sheets import load_sheet_df, save_annotation, append_annotation_row, get_current_index, progress_stats

try:
    from streamlit_image_zoom import image_zoom
    HAS_ZOOM = True
except ImportError:
    HAS_ZOOM = False

# ── Label vocabulary ──────────────────────────────────────────────────────────

MEMBRES = [
    "Left Front (LF or L)",
    "Right Front (RF or R)",
    "Left Hind (LH)",
    "Right Hind (RH)",
]

ZONES = [
    "Front Foot",
    "Front Fetlock",
    "Knee or Carpus",
    "Hind Fetlock",
    "Hock or Tarsus",
    "Stifle",
    "Cervical spine",
    "Dorsal Spinous Processes",
]

VUES = [
    "AP",
    "Lateral",
    "Navicular DV",
    "Navicular Skyline",
    "Lateral Oblique",
    "Medial Oblique",
    "Flexed Lateral",
    "Laterals",
]

# Maps dropdown label → short English term used in manual_label
_MEMBRE_SHORT = {
    "Left Front (LF or L)":  "Left Front",
    "Right Front (RF or R)": "Right Front",
    "Left Hind (LH)":        "Left Hind",
    "Right Hind (RH)":       "Right Hind",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _header():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Portal"):
            st.session_state.module = None
            st.session_state.auth.pop("horse", None)
            st.rerun()
    with col_title:
        st.markdown("## 🐴 Horse X-Ray Annotation")


def _default(options, saved_val):
    """Return index of saved_val in options, or 0 if not found."""
    try:
        return options.index(str(saved_val)) if str(saved_val) in options else 0
    except ValueError:
        return 0

def _pdf_viewer(report_id: str, report_name: str):
    """
    Download the PDF via the service account, convert each page to an image
    with pymupdf, and display with st.image() — same approach as X-ray images.
    """
    with st.spinner("Loading report…"):
        try:
            pdf_bytes = load_pdf_from_drive(report_id)
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            for page in doc:
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                if HAS_ZOOM:
                    image_zoom(img, mode="mousemove", size=500, zoom_factor=4)
                else:
                    st.image(img, use_container_width=True)
        except Exception as exc:
            st.warning(f"Could not load PDF ({exc}).")
            st.link_button(
                f"📄 {report_name or 'Open report'}",
                f"https://drive.google.com/file/d/{report_id}/view",
            )

# ── Main entry point ──────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id   = st.secrets["sheets"]["horse_sheet_id"]
    sheet_name = st.secrets["sheets"]["horse_sheet_name"]

    # ── Load sheet ────────────────────────────────────────────────────────────
    with st.spinner("Loading annotation data…"):
        df = load_sheet_df(sheet_id, sheet_name)

    if df.empty:
        st.error("⚠️ Google Sheet is empty or unreachable. Check your secrets.")
        return

    done_count, total = progress_stats(df)
    current_idx = get_current_index(df)

    # Progress bar
    progress_val = done_count / total if total else 1.0
    st.progress(progress_val)
    st.caption(f"**{done_count} / {total}** images annotated — {total - done_count} remaining")

    # ── All done ──────────────────────────────────────────────────────────────
    if current_idx is None:
        st.success("✅ All images have been annotated!")
        st.balloons()
        return

    row = df.iloc[current_idx]

    # ── Load X-ray image from Drive ───────────────────────────────────────────
    with st.spinner("Loading image from Google Drive…"):
        try:
            img = load_image_from_drive(str(row["image_id"]))
        except Exception as e:
            st.error(f"Could not load image `{row.get('anonymized_image', row['image_id'])}`: {e}")
            return

    img_display = resize_for_display(img, max_px=600)

    # ── Read consultation date (pre-filled in Sheet, read-only) ──────────────
    consultation_date = str(row.get("consultation_date", "")).strip()
    # Also accept the capitalised variant used in legacy sheets
    if not consultation_date:
        consultation_date = str(row.get("Consultation Date", "")).strip()

    # ── Three-column layout: X-ray | PDF | Form ───────────────────────────────
    col_img, col_pdf, col_form = st.columns([1.3, 1.5, 1])

    # ── Left: X-ray image ─────────────────────────────────────────────────────
    with col_img:
        st.markdown(f"**Image:** `{row.get('anonymized_image', row['image_id'])}`")
        if consultation_date:
            st.markdown(
                f"<div style='background:#1a3a5c; border-left:4px solid #4F8BF9; "
                f"border-radius:6px; padding:6px 12px; margin-bottom:8px; font-size:0.9rem;'>"
                f"📅 <b>Consultation date:</b>&nbsp; {consultation_date}</div>",
                unsafe_allow_html=True,
            )
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=550, zoom_factor=2.5)
        else:
            st.image(img_display, use_container_width=True)

    # ── Centre: embedded PDF report ───────────────────────────────────────────
    with col_pdf:
        report_id = str(row.get("report_id", "")).strip()
        if report_id:
            report_name = row.get("anonymized_report", "Report")
            header_parts = [f"**Report:** `{report_name}`"]
            if consultation_date:
                header_parts.append(
                    f"&nbsp;&nbsp;📅 <span style='color:#4F8BF9; font-weight:600;'>{consultation_date}</span>"
                )
            st.markdown("  ".join(header_parts), unsafe_allow_html=True)
            if consultation_date:
                st.caption("⬆ Scroll to this date in the report to find the relevant section.")
            if report_name.lower().endswith(".txt"):
                with st.spinner("Loading report…"):
                    try:
                        raw = load_pdf_from_drive(report_id)
                        st.text(raw.decode("utf-8", errors="replace"))
                    except Exception as exc:
                        st.warning(f"Could not load report ({exc}).")
            else:
                _pdf_viewer(report_id, report_name)

        else:
            st.info("No report linked to this image.")

    # ── Right: annotation form ────────────────────────────────────────────────
    with col_form:
        st.markdown("### Annotation")
        if consultation_date:
            st.markdown(
                f"<div style='background:#1a3a5c; border-left:4px solid #4F8BF9; "
                f"border-radius:6px; padding:5px 10px; margin-bottom:6px; font-size:0.85rem;'>"
                f"📅 {consultation_date}</div>",
                unsafe_allow_html=True,
            )
        st.markdown("---")

        membre = st.selectbox(
            "🐴 Limb (Membre)",
            MEMBRES,
            index=_default(MEMBRES, row.get("membre", "")),
            key=f"horse_membre_{current_idx}",
        )
        zone = st.selectbox(
            "🦴 Body part (Zone)",
            ZONES,
            index=_default(ZONES, row.get("body_part", "")),
            key=f"horse_zone_{current_idx}",
        )
        vue = st.selectbox(
            "📐 Radiographic view (Vue)",
            VUES,
            index=_default(VUES, row.get("view", "")),
            key=f"horse_vue_{current_idx}",
        )

        # Auto-compose label, but let the annotator edit it
        auto_label = f"{_MEMBRE_SHORT[membre]} {zone} | {vue}"
        saved_label = str(row.get("label", "")).strip()
        # Use the saved label if it exists and differs from the auto one,
        # otherwise use the freshly generated one
        initial_label = saved_label if saved_label else auto_label

        st.markdown("---")

        manual_label = st.text_input(
            "✏️ Label (editable)",
            #value=initial_label,
            key=f"horse_label_{current_idx}",
            help="Anomaly described in the report.",
            placeholder="Eg: Lipping, Bone remodeling, Fragment, etc."
        )

        # Report description (free text)
        saved_desc = str(row.get("custom_report", "")).strip()
        report_description = st.text_area(
            "📝 Report description",
            value=saved_desc,
            height=120,
            key=f"horse_desc_{current_idx}",
            placeholder="Copy/Paste the detailed report description…",
        )

        st.markdown("")  # spacing

        # ── Extra labels (multiple anomalies) ─────────────────────────────────
        extra_ids_key = f"horse_extra_labels_{current_idx}"
        if extra_ids_key not in st.session_state:
            st.session_state[extra_ids_key] = []

        if st.session_state[extra_ids_key]:
            st.markdown("**Additional labels:**")
        for uid in list(st.session_state[extra_ids_key]):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.text_input(
                    "✏️ Extra label",
                    key=f"horse_extra_{uid}",
                    placeholder="Eg: Lipping, Bone remodeling, Fragment, etc.",
                    label_visibility="collapsed",
                )
            with c2:
                if st.button("✕", key=f"horse_rm_{uid}"):
                    st.session_state[extra_ids_key].remove(uid)
                    st.rerun()

        if st.button("➕ Add Label", key=f"horse_add_label_{current_idx}"):
            import uuid
            st.session_state[extra_ids_key].append(str(uuid.uuid4())[:8])
            st.rerun()

        st.markdown("")  # spacing

        if st.button("💾 Save & Next →", use_container_width=True, key=f"horse_save_{current_idx}"):
            save_annotation(
                sheet_id,
                sheet_name,
                current_idx,
                {
                    "membre":        membre,
                    "body_part":     zone,
                    "view":          vue,
                    "label":         manual_label,
                    "custom_report": report_description,
                },
            )

            # Append one new row per extra label
            extra_labels = [
                st.session_state.get(f"horse_extra_{uid}", "").strip()
                for uid in st.session_state.get(extra_ids_key, [])
                if st.session_state.get(f"horse_extra_{uid}", "").strip()
            ]
            source_row = row.to_dict()
            for extra_label in extra_labels:
                append_annotation_row(
                    sheet_id,
                    sheet_name,
                    source_row,
                    {
                        "membre":        membre,
                        "body_part":     zone,
                        "view":          vue,
                        "label":         extra_label,
                        "custom_report": report_description,
                    },
                )

            # Reset extra labels for this image
            st.session_state[extra_ids_key] = []
            st.rerun()
