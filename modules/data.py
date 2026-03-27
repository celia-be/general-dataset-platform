"""
Data Annotation Validation module — faithful migration of the original app.py.

Preserves the original UX exactly:
  • Zoom panel (hover to zoom)
  • Click panel (place red bounding boxes on anomalies)
  • Bbox preview + coordinate list
  • Proposed label pre-filled, annotator corrects it
  • Report description shown below the panels

All data now lives in Google Drive (images) + Google Sheets (CSV),
giving automatic session persistence — no more lost progress on sleep/restart.

Expected Google Sheet columns:
  image_id | image_name | proposed_label | report_description |
  confirmed_label | bbox | status | annotated_at
"""

import json
import streamlit as st
from PIL import ImageDraw
from utils.google_drive import load_image_from_drive, resize_for_display
from utils.google_sheets import load_sheet_df, save_annotation, get_current_index, progress_stats

try:
    from streamlit_image_zoom import image_zoom
    HAS_ZOOM = True
except ImportError:
    HAS_ZOOM = False

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
    HAS_COORDS = True
except ImportError:
    HAS_COORDS = False

BBOX_HALF = 20  # matches original app.py box_size=40 (half = 20)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _header():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Portal"):
            st.session_state.module = None
            st.session_state.auth.pop("data", None)
            st.session_state.pop("data_clicks", None)
            st.rerun()
    with col_title:
        st.markdown("## 📊 Data Annotation Validation")


def _draw_boxes(img, clicks):
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for pt in clicks:
        x, y = pt["x"], pt["y"]
        draw.rectangle(
            [(x - BBOX_HALF, y - BBOX_HALF), (x + BBOX_HALF, y + BBOX_HALF)],
            outline="red",
            width=3,
        )
    return out


# ── Main entry point ─────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id   = st.secrets["sheets"]["data_sheet_id"]
    sheet_name = st.secrets["sheets"]["data_sheet_name"]

    # ── bbox session state ────────────────────────────────────────────────────
    if "data_clicks" not in st.session_state:
        st.session_state.data_clicks = []

    # ── Load sheet ────────────────────────────────────────────────────────────
    with st.spinner("Loading annotation data…"):
        df = load_sheet_df(sheet_id, sheet_name)

    if df.empty:
        st.error("⚠️ Google Sheet is empty or unreachable. Check your secrets.")
        return

    done_count, total = progress_stats(df)
    current_idx = get_current_index(df)

    st.progress(done_count / total if total else 1.0)

    if current_idx is None:
        st.success("✅ All images have been annotated!")
        st.balloons()
        return

    row = df.iloc[current_idx]

    # Reset bbox when image changes
    if st.session_state.get("data_last_idx") != current_idx:
        st.session_state.data_clicks = []
        st.session_state.data_last_idx = current_idx

    # ── Load image ────────────────────────────────────────────────────────────
    with st.spinner("Loading image…"):
        try:
            img = load_image_from_drive(str(row["image_id"]))
        except Exception as e:
            st.error(f"Could not load image: {e}")
            return

    img_display = resize_for_display(img, max_px=400)

    # ── Three-column layout identical to original app.py ─────────────────────
    col_zoom, col_click, col_preview = st.columns([1.3, 1.3, 0.9])

    # Zoom column
    with col_zoom:
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=600, zoom_factor=2.2)
        else:
            st.image(img_display, use_container_width=True)

    # Click column
    with col_click:
        st.markdown("""### Mark the anomalies
        Click on each visible anomaly in the image to place a red box.
        You can click multiple times.
        Use **Modify boxes** to reset your selection.
        """)

        if HAS_COORDS:
            coords = streamlit_image_coordinates(img_display, key=f"data_click_{current_idx}")
            if coords:
                new_pt = {"x": coords["x"], "y": coords["y"]}
                if not st.session_state.data_clicks or st.session_state.data_clicks[-1] != new_pt:
                    st.session_state.data_clicks.append(new_pt)
        else:
            st.info("Install `streamlit-image-coordinates` to enable bbox clicking.")

        if st.button("Modify boxes", key=f"data_clear_{current_idx}"):
            st.session_state.data_clicks = []

    # Preview column
    with col_preview:
        st.markdown("**Preview & coordinates**")
        if st.session_state.data_clicks:
            preview = _draw_boxes(img_display, st.session_state.data_clicks)
            st.image(preview, caption="BBox preview", use_container_width=True)
            st.markdown("**Box Coordinates:**")
            for i, pt in enumerate(st.session_state.data_clicks):
                st.markdown(
                    f"• Box {i+1}: `x={pt['x']}`, `y={pt['y']}`, `width=40`, `height=40`"
                )
        else:
            st.image(img_display, use_container_width=True)

    # ── Report description + label form ──────────────────────────────────────
    report_desc = str(row.get("report_description", "") or "")
    if report_desc:
        st.markdown(f"**Report description:** {report_desc}")
    else:
        st.markdown("_No description available_")

    proposed = str(row.get("proposed_label", "") or "")
    saved_confirmed = str(row.get("confirmed_label", "") or "")
    prefill = saved_confirmed if saved_confirmed else proposed

    confirmed_label = st.text_input(
        "Correct label:",
        value=prefill,
        key=f"data_label_{current_idx}",
    )

    if st.button("Save & Next", key=f"data_save_{current_idx}"):
        bbox_list = [
            {"x": pt["x"], "y": pt["y"], "width": BBOX_HALF * 2, "height": BBOX_HALF * 2}
            for pt in st.session_state.data_clicks
        ]
        save_annotation(
            sheet_id,
            sheet_name,
            current_idx,
            {
                "confirmed_label": confirmed_label,
                "bbox":            json.dumps(bbox_list) if bbox_list else "",
            },
        )
        st.session_state.data_clicks = []
        st.rerun()
