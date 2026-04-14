"""
Horse X-Ray Annotation module.

What the annotator does:
  1. View the X-ray image (with zoom)
  2. Download the PDF report directly from Google Drive (via service account —
     no Google login required on the client side)
  3. Select Membre / Body part / View from dropdowns
  4. Click "Save & Next" → writes manual_label to Google Sheets

Expected Google Sheet columns:
  image_id | image_name | report_id | report_name |
  membre | body_part | view | manual_label | custom_report | status | annotated_at
"""

import streamlit as st
from utils.google_drive import load_image_from_drive, load_pdf_from_drive, resize_for_display
from utils.google_sheets import load_sheet_df, save_annotation, get_current_index, progress_stats

try:
    from streamlit_image_zoom import image_zoom
    HAS_ZOOM = True
except ImportError:
    HAS_ZOOM = False

# ── Label vocabulary ──────────────────────────────────────────────────────────

MEMBRES = ["Left Front (LF or L)", "Right Front (RF or R)", "Left Hind (LH)", "Right Hind (RH)"]

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


def _pdf_button(report_id: str, report_name: str):
    """
    Download the PDF via the service account and offer it as a Streamlit
    download button — no Google login required on the client side.

    Falls back to a plain Drive link if the download fails (e.g. wrong ID).
    """
    with st.spinner("Loading report…"):
        try:
            pdf_bytes = load_pdf_from_drive(report_id)
            filename  = (report_name or "rapport").strip()
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            st.download_button(
                label=f"📄 {report_name or 'Download report'}",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
            )
        except Exception as exc:
            # Fallback: plain link (still requires Google login if file is private,
            # but at least shows something useful)
            st.warning(f"Could not pre-load PDF ({exc}). Opening via Drive link instead.")
            st.link_button(
                f"📄 {report_name or 'Open report'}",
                f"https://drive.google.com/file/d/{report_id}/view",
            )


# ── Main entry point ──────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id   = st.secrets["sheets"]["horse_sheet_id"]
    sheet_name = st.secrets["sheets"]["horse_sheet_name"]

    with st.spinner("Loading annotation data…"):
        df = load_sheet_df(sheet_id, sheet_name)

    if df.empty:
        st.error("⚠️ Google Sheet is empty or unreachable. Check your secrets.")
        return

    done_count, total = progress_stats(df)
    current_idx       = get_current_index(df)

    progress_val = done_count / total if total else 1.0
    st.progress(progress_val)
    st.caption(f"**{done_count} / {total}** images annotated — {total - done_count} remaining")

    if current_idx is None:
        st.success("✅ All images have been annotated!")
        st.balloons()
        return

    row = df.iloc[current_idx]

    # ── Load image ────────────────────────────────────────────────────────────
    with st.spinner("Loading image from Google Drive…"):
        try:
            img = load_image_from_drive(str(row["image_id"]))
        except Exception as e:
            st.error(f"Could not load image `{row.get('image_name', row['image_id'])}`: {e}")
            return

    img_display = resize_for_display(img, max_px=600)

    # ── Layout ────────────────────────────────────────────────────────────────
    col_img, col_form = st.columns([1.6, 1])

    with col_img:
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=580, zoom_factor=2.5)
        else:
            st.image(img_display, use_container_width=True)

        # PDF report — served via service account, no Google login needed
        report_id = str(row.get("report_id", "")).strip()
        if report_id:
            report_name = str(row.get("report_name", "")).strip()
            _pdf_button(report_id, report_name)

    with col_form:
        st.markdown(f"**Image:** `{row.get('image_name', row['image_id'])}`")
        st.markdown("---")

        def _default(options, saved_val):
            try:
                return options.index(str(saved_val)) if str(saved_val) in options else 0
            except ValueError:
                return 0

        membre = st.selectbox(
            "🐴 Limb",
            MEMBRES,
            index=_default(MEMBRES, row.get("membre", "")),
            key=f"horse_membre_{current_idx}",
        )
        body_part = st.selectbox(
            "🦴 Body part",
            ZONES,
            index=_default(ZONES, row.get("body_part", "")),
            key=f"horse_body_part_{current_idx}",
        )
        view = st.selectbox(
            "📐 View",
            VUES,
            index=_default(VUES, row.get("view", "")),
            key=f"horse_view_{current_idx}",
        )

        manual_label = f"{_MEMBRE_SHORT[membre]} {body_part} | {view}"

        st.markdown("---")
        st.info(f"**Generated label:**  `{manual_label}`")

        saved_custom  = str(row.get("custom_report", "")).strip()
        custom_report = st.text_area(
            "📝 Custom report notes",
            value=saved_custom,
            height=100,
            key=f"horse_custom_report_{current_idx}",
        )

        st.markdown("")

        if st.button("💾 Save & Next →", use_container_width=True, key=f"horse_save_{current_idx}"):
            save_annotation(
                sheet_id,
                sheet_name,
                current_idx,
                {
                    "membre":        membre,
                    "body_part":     body_part,
                    "view":          view,
                    "manual_label":  manual_label,
                    "custom_report": custom_report,
                },
            )
            st.rerun()
