"""
Horse X-Ray Annotation module.

What the annotator does:
  1. View the X-ray image (with zoom)
  2. Open the PDF report in Google Drive
  3. Select Membre / Zone / Vue from dropdowns
  4. Click "Save & Next" → writes manual_label to Google Sheets

Progress is persisted in Google Sheets: on reload, the app resumes
from the first row where status != 'done'.

Expected Google Sheet columns:
  image_id | image_name | report_id | report_name |
  membre | zone | vue | manual_label | status | annotated_at
"""

import streamlit as st
from utils.google_drive import load_image_from_drive, drive_view_url, resize_for_display
from utils.google_sheets import load_sheet_df, save_annotation, get_current_index, progress_stats

try:
    from streamlit_image_zoom import image_zoom
    HAS_ZOOM = True
except ImportError:
    HAS_ZOOM = False

# ── Label vocabulary ─────────────────────────────────────────────────────────

MEMBRES = ["Left Front (LF or L)", "Right Front (RF or R)", "Left Hind (LH)", "Right Hind (RH)"]

ZONES = [
    "Front Foot",
    "Front Fetlock",
    "Knee",
    "Hind Fetlock",
    "Hock",
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
    "Left Front (LF)":  "Left Front",
    "Right Front (RF)": "Right Front",
    "Left Hind (LH)":   "Left Hind",
    "Right Hind (RH)":  "Right Hind",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def _header():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Portal"):
            st.session_state.module = None
            st.session_state.auth.pop("horse", None)
            st.rerun()
    with col_title:
        st.markdown("## 🐴 Horse X-Ray Annotation")


# ── Main entry point ─────────────────────────────────────────────────────────

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

    # ── Load image from Drive ────────────────────────────────────────────────
    with st.spinner("Loading image from Google Drive…"):
        try:
            img = load_image_from_drive(str(row["image_id"]))
        except Exception as e:
            st.error(f"Could not load image `{row.get('image_name', row['image_id'])}`: {e}")
            return

    img_display = resize_for_display(img, max_px=600)

    # ── Layout: image left | form right ──────────────────────────────────────
    col_img, col_form = st.columns([1.6, 1])

    with col_img:
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=580, zoom_factor=2.5)
        else:
            st.image(img_display, use_container_width=True)

        # Link to PDF report if available
        report_id = str(row.get("report_id", "")).strip()
        if report_id:
            report_name = row.get("report_name", "Open report")
            st.link_button(f"📄 {report_name}", drive_view_url(report_id))

    with col_form:
        st.markdown(f"**Image:** `{row.get('image_name', row['image_id'])}`")
        st.markdown("---")

        # Pre-fill dropdowns with previously saved values (if re-editing)
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
        zone = st.selectbox(
            "🦴 Body part",
            ZONES,
            index=_default(ZONES, row.get("zone", "")),
            key=f"horse_zone_{current_idx}",
        )
        vue = st.selectbox(
            "📐 Vue radiographique",
            VUES,
            index=_default(VUES, row.get("vue", "")),
            key=f"horse_vue_{current_idx}",
        )

        # Auto-compose the manual_label exactly as expected by the CSV spec
        manual_label = f"{_MEMBRE_SHORT[membre]} {zone} | {vue}"

        st.markdown("---")
        st.info(f"**Label généré :**  `{manual_label}`")

        st.markdown("")  # spacing

        if st.button("💾 Save & Next →", use_container_width=True, key=f"horse_save_{current_idx}"):
            save_annotation(
                sheet_id,
                sheet_name,
                current_idx,
                {
                    "membre":       membre,
                    "zone":         zone,
                    "vue":          vue,
                    "manual_label": manual_label,
                },
            )
            st.rerun()
