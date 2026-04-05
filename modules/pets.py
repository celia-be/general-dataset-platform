"""
Pets Annotation module — labels + bounding boxes.

What the annotator does:
  1. View the image (zoom panel + clickable panel for bboxes)
  2. Select Species / Body part / View from dropdowns
  3. Optionally type a free-text label
  4. Click on the image to place bounding boxes (red squares)
  5. Click "Save & Next" → writes everything to Google Sheets

Handles thousands of images: images are fetched from Google Drive one at a time.

Expected Google Sheet columns:
  image_id | image_name | species | body_part | view |
  confirmed_label | bbox | status | annotated_at
  (add any extra columns you need — they will be ignored by the app)
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

# ── Label vocabulary (customise freely) ─────────────────────────────────────

SPECIES = ["Dog", "Cat", "Rabbit", "Bird", "Ferret", "Guinea pig", "Other"]

BODY_PARTS = [
    "Head / Skull",
    "Neck / Cervical spine",
    "Chest / Thorax",
    "Abdomen",
    "Pelvis",
    "Front limb — Shoulder",
    "Front limb — Elbow",
    "Front limb — Carpus / Wrist",
    "Front limb — Foot",
    "Hind limb — Hip",
    "Hind limb — Stifle / Knee",
    "Hind limb — Tarsus / Ankle",
    "Hind limb — Foot",
    "Spine — Thoracic",
    "Spine — Lumbar",
    "Whole body",
]

VIEWS = [
    "Lateral (profile)",
    "AP / VD (ventrodorsal)",
    "DV (dorsoventral)",
    "Oblique",
    "Skyline / Tangential",
    "Other",
]

BBOX_SIZE = 40   # half-side of the red box in pixels (on resized image)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _header():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Portal"):
            st.session_state.module = None
            st.session_state.auth.pop("pets", None)
            st.session_state.pop("pets_clicks", None)
            st.rerun()
    with col_title:
        st.markdown("## 🐾 Pets Annotation")


def _draw_boxes(img, clicks, box_size=BBOX_SIZE):
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for pt in clicks:
        x, y = pt["x"], pt["y"]
        draw.rectangle(
            [(x - box_size, y - box_size), (x + box_size, y + box_size)],
            outline="red",
            width=3,
        )
    return out


def _default_idx(options, saved):
    try:
        return options.index(str(saved)) if str(saved) in options else 0
    except ValueError:
        return 0


# ── Main entry point ─────────────────────────────────────────────────────────

def show():
    _header()

    sheet_id   = st.secrets["sheets"]["pets_sheet_id"]
    sheet_name = st.secrets["sheets"]["pets_sheet_name"]

    # ── Session state for bbox clicks ─────────────────────────────────────────
    if "pets_clicks" not in st.session_state:
        st.session_state.pets_clicks = []

    # ── Load sheet ────────────────────────────────────────────────────────────
    with st.spinner("Loading annotation data…"):
        df = load_sheet_df(sheet_id, sheet_name)

    if df.empty:
        st.error("⚠️ Google Sheet is empty or unreachable. Check your secrets.")
        return

    done_count, total = progress_stats(df)
    current_idx = get_current_index(df)

    st.progress(done_count / total if total else 1.0)
    st.caption(f"**{done_count} / {total}** images annotated — {total - done_count} remaining")

    if current_idx is None:
        st.success("✅ All images have been annotated!")
        st.balloons()
        return

    row = df.iloc[current_idx]

    # Reset click state when image changes
    if st.session_state.get("pets_last_idx") != current_idx:
        st.session_state.pets_clicks = []
        st.session_state.pets_last_idx = current_idx

    # ── Load image ────────────────────────────────────────────────────────────
    with st.spinner("Loading image from Google Drive…"):
        try:
            img = load_image_from_drive(str(row["image_id"]))
        except Exception as e:
            st.error(f"Could not load image: {e}")
            return

    img_display = resize_for_display(img, max_px=500)

    # ── Three-column layout: zoom | click | preview+form ─────────────────────
    col_zoom, col_click, col_right = st.columns([1.3, 1.3, 1.1])

    # Column 1 — zoom on hover
    with col_zoom:
        st.caption("🔍 Hover to zoom")
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=500, zoom_factor=3.5)
        else:
            st.image(img_display, use_container_width=True)

    # Column 2 — click to place bboxes
    with col_click:
        st.markdown("### Mark the anomalies")
        st.caption("Click on each anomaly to place a red box. Use **Modify boxes** to reset.")

        if HAS_COORDS:
            coords = streamlit_image_coordinates(img_display, key=f"pets_click_{current_idx}")
            if coords:
                new_pt = {"x": coords["x"], "y": coords["y"]}
                if not st.session_state.pets_clicks or st.session_state.pets_clicks[-1] != new_pt:
                    st.session_state.pets_clicks.append(new_pt)
        else:
            st.info("Install `streamlit-image-coordinates` to enable bbox clicking.")

        if st.button("✏️ Modify boxes", key=f"pets_clear_{current_idx}"):
            st.session_state.pets_clicks = []

    # Column 3 — preview + annotation form
    with col_right:
        # BBox preview
        if st.session_state.pets_clicks:
            preview = _draw_boxes(img_display, st.session_state.pets_clicks)
            st.image(preview, caption="Bbox preview", use_container_width=True)
            st.markdown("**Coordinates:**")
            for i, pt in enumerate(st.session_state.pets_clicks):
                st.markdown(f"• Box {i+1}: `x={pt['x']}` `y={pt['y']}` `w/h={BBOX_SIZE*2}`")
        else:
            st.image(img_display, caption="No boxes yet", use_container_width=True)

        st.markdown("---")

        # Annotation dropdowns
        species = st.selectbox(
            "🐾 Species",
            SPECIES,
            index=_default_idx(SPECIES, row.get("species", "")),
            key=f"pets_species_{current_idx}",
        )
        body_part = st.selectbox(
            "🦴 Body part",
            BODY_PARTS,
            index=_default_idx(BODY_PARTS, row.get("body_part", "")),
            key=f"pets_bodypart_{current_idx}",
        )
        view = st.selectbox(
            "📐 View",
            VIEWS,
            index=_default_idx(VIEWS, row.get("view", "")),
            key=f"pets_view_{current_idx}",
        )
        confirmed_label = st.text_input(
            "✏️ Label (anomaly seen on radiograph)",
            value=str(row.get("confirmed_label", "") or ""),
            key=f"pets_label_{current_idx}",
        )

        st.markdown("")
        if st.button("💾 Save & Next →", use_container_width=True, key=f"pets_save_{current_idx}"):
            bbox_list = [
                {"x": pt["x"], "y": pt["y"], "width": BBOX_SIZE * 2, "height": BBOX_SIZE * 2}
                for pt in st.session_state.pets_clicks
            ]
            save_annotation(
                sheet_id,
                sheet_name,
                current_idx,
                {
                    "species":         species,
                    "body_part":       body_part,
                    "view":            view,
                    "confirmed_label": confirmed_label,
                    "bbox":            json.dumps(bbox_list) if bbox_list else "",
                },
            )
            st.session_state.pets_clicks = []
            st.rerun()
