"""
Cheval Upload — Horse X-Ray Upload & Annotation module.

Workflow:
  1. Upload one or several X-ray images (PNG/JPEG) in one go.
  2. Each image is anonymised automatically in the backend and uploaded to GCS
     individually with its own independent HTTP request.
  3. Each anonymised image is registered as a "pending" row in Google Sheets.
  4. The user annotates images one by one: hover to zoom, click to place boxes,
     enter a label, save.
  5. Save → updates the corresponding Sheets row to "done".

Expected Google Sheet columns:
  image_id | image_name | label | bbox | status | uploaded_at | annotated_at
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

THUMB_MAX       = 500   # max side (px) for the display thumbnail
BBOX_SIZE       = 15    # half-side of bounding box in px (thumbnail space)
ANON_CROP_RATIO = 0.08  # fraction of min(w, h) cropped from each edge


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
    for key in ["cheval_step", "cheval_queue", "cheval_queue_pos", "cheval_clicks"]:
        st.session_state.pop(key, None)


def _anonymize(img: Image.Image) -> Image.Image:
    """Crop top+bottom bands proportional to min(w, h) to remove metadata."""
    w, h    = img.size
    crop_px = max(1, int(min(w, h) * ANON_CROP_RATIO))
    crop_px = min(crop_px, h // 4)
    return img.crop((0, crop_px, w, h - crop_px))


def _make_thumbnail(img: Image.Image) -> Image.Image:
    out = img.copy()
    out.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
    return out


def _draw_boxes(img: Image.Image, clicks: list) -> Image.Image:
    out  = img.copy()
    draw = ImageDraw.Draw(out)
    for pt in clicks:
        x, y = pt["x"], pt["y"]
        draw.rectangle(
            [(x - BBOX_SIZE, y - BBOX_SIZE), (x + BBOX_SIZE, y + BBOX_SIZE)],
            outline="red", width=2,
        )
    return out


def _inject_hover_zoom():
    """
    Inject JS that adds a hover-zoom (2.5×) to the streamlit_image_coordinates
    iframe. CSS transform is purely visual — offsetX/Y click coordinates stay
    in the original image space so no conversion is needed.
    """
    st.markdown(
        """
        <script>
        (function () {
            var ZOOM = 2.5;
            var done = new WeakSet();
            function inject(iframe) {
                if (done.has(iframe)) return;
                try {
                    var doc = iframe.contentDocument;
                    if (!doc || doc.readyState !== 'complete') return;
                    // Target only the image-coordinates iframe:
                    // its <body> has an <img> as a direct child.
                    var img = null;
                    for (var i = 0; i < doc.body.children.length; i++) {
                        var t = doc.body.children[i].tagName;
                        if (t === 'IMG' || t === 'CANVAS') { img = doc.body.children[i]; break; }
                    }
                    if (!img) return;
                    done.add(iframe);
                    var s = doc.createElement('style');
                    s.textContent = 'html,body{margin:0;padding:0;overflow:hidden}' +
                        'img,canvas{display:block;cursor:crosshair!important;' +
                        'transition:transform 0.07s ease;will-change:transform}';
                    doc.head.appendChild(s);
                    doc.addEventListener('mousemove', function(e) {
                        var r = img.getBoundingClientRect();
                        if (!r.width) return;
                        var px = ((e.clientX - r.left) / r.width  * 100).toFixed(1);
                        var py = ((e.clientY - r.top)  / r.height * 100).toFixed(1);
                        img.style.transformOrigin = px + '% ' + py + '%';
                        img.style.transform = 'scale(' + ZOOM + ')';
                    }, {passive: true});
                    doc.addEventListener('mouseleave', function() {
                        img.style.transform = 'scale(1)';
                        img.style.transformOrigin = '50% 50%';
                    });
                } catch(e) {}
            }
            function scan() { document.querySelectorAll('iframe').forEach(inject); }
            new MutationObserver(scan).observe(document.body, {childList:true, subtree:true});
            scan();
            var n = 0, t = setInterval(function(){ scan(); if(++n>40) clearInterval(t); }, 200);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


# ── Upload helper — one image at a time ───────────────────────────────────────

def _upload_one(uploaded, i: int, sheet_id: str, sheet_name: str, bucket_name: str) -> dict:
    """
    Process a single UploadedFile: anonymise → GCS → Sheets.
    Returns a queue-entry dict on success, raises on error.

    Each call is fully self-contained: it reads raw bytes immediately,
    creates an independent image object, and calls upload_pil_image_to_gcs
    which itself creates a fresh GCS client — no shared state between images.
    """
    # 1. Read bytes eagerly — avoids PIL lazy-open issues with Streamlit file objects
    raw = uploaded.read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")

    # 2. Anonymise
    anon = _anonymize(img)

    # 3. Build a unique filename
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem     = uploaded.name.rsplit(".", 1)[0]
    filename = f"{stem}_anon_{ts}_{i:03d}.png"

    # 4. Upload to GCS — upload_pil_image_to_gcs creates a fresh client each call
    file_id = upload_pil_image_to_gcs(anon, filename, bucket_name)

    # 5. Register in Sheets
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

    # 6. Serialise anonymised image for session state
    buf = io.BytesIO()
    anon.save(buf, format="PNG")

    return {
        "image_bytes": buf.getvalue(),
        "file_id":     file_id,
        "file_name":   filename,
        "sheet_idx":   sheet_idx,
    }


# ── Step 1 : Upload ───────────────────────────────────────────────────────────

def _show_upload(sheet_id: str, sheet_name: str, bucket_name: str):
    st.markdown("### Étape 1 — Chargement des images")
    st.caption(
        "Sélectionner une ou plusieurs radiographies. "
        "L'anonymisation (suppression des bandes haut/bas) est automatique."
    )

    uploaded_files = st.file_uploader(
        "Sélectionner des radiographies (PNG ou JPEG)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="cheval_uploader",
    )

    if not uploaded_files:
        return

    n      = len(uploaded_files)
    plural = "s" if n > 1 else ""
    st.info(f"**{n} image{plural}** sélectionnée{plural}.")

    if not st.button(f"☁️ Uploader {n} image{plural} et annoter →", key="cheval_upload_btn"):
        return

    queue        = []
    errors       = []
    progress_bar = st.progress(0)
    status_text  = st.empty()

    for i, uploaded in enumerate(uploaded_files):
        status_text.text(f"Upload {i + 1}/{n} : {uploaded.name}…")
        try:
            entry = _upload_one(uploaded, i, sheet_id, sheet_name, bucket_name)
            queue.append(entry)
        except Exception as exc:
            errors.append(f"❌ {uploaded.name} : {exc}")
        progress_bar.progress((i + 1) / n)

    if errors:
        for msg in errors:
            st.error(msg)

    if not queue:
        st.error("Aucune image n'a pu être uploadée.")
        return

    ok = len(queue)
    status_text.text(f"✅ {ok}/{n} image{'s' if ok > 1 else ''} uploadée{'s' if ok > 1 else ''} avec succès !")

    st.session_state.cheval_queue     = queue
    st.session_state.cheval_queue_pos = 0
    st.session_state.cheval_clicks    = []
    st.session_state.cheval_step      = "annotate"
    st.rerun()


# ── Step 2 : Annotation (queue) ───────────────────────────────────────────────

def _show_annotate(sheet_id: str, sheet_name: str):
    _inject_hover_zoom()

    queue = st.session_state.cheval_queue
    pos   = st.session_state.cheval_queue_pos
    total = len(queue)

    if pos >= total:
        st.success(f"✅ Toutes les {total} images ont été annotées !")
        if st.button("⬆️ Uploader d'autres images"):
            _clear_state()
            st.rerun()
        return

    current   = queue[pos]
    file_name = current["file_name"]

    img         = Image.open(io.BytesIO(current["image_bytes"]))
    img_display = _make_thumbnail(img)

    st.progress(pos / total)
    st.markdown(f"### Étape 2 — Annotation : image **{pos + 1} / {total}**")
    st.caption(f"`{file_name}`")

    col_click, col_preview, col_form = st.columns([1.3, 1.3, 1.1])

    with col_click:
        st.caption("🖱️ Survoler pour zoomer · Cliquer pour placer une box")
        if HAS_COORDS:
            coords = streamlit_image_coordinates(img_display, key=f"cheval_click_img_{pos}")
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

    with col_preview:
        st.caption("👁️ Aperçu avec boxes")
        clicks = st.session_state.cheval_clicks
        if clicks:
            st.image(_draw_boxes(img_display, clicks), width="stretch")
            st.markdown("**Coordonnées :**")
            for i, pt in enumerate(clicks):
                st.markdown(f"• Box {i+1} : `x={pt['x']}` `y={pt['y']}` `taille={BBOX_SIZE*2}px`")
        else:
            st.image(img_display, width="stretch")
            st.caption("Aucune box pour l'instant.")

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

        if st.button(f"💾 Sauvegarder & {next_label}", use_container_width=True, key=f"cheval_save_{pos}"):
            bbox_list = [
                {"x": pt["x"], "y": pt["y"], "width": BBOX_SIZE*2, "height": BBOX_SIZE*2}
                for pt in st.session_state.cheval_clicks
            ]
            save_annotation(
                sheet_id, sheet_name, current["sheet_idx"],
                {"label": label, "bbox": json.dumps(bbox_list) if bbox_list else ""},
            )
            st.session_state.cheval_queue_pos += 1
            st.session_state.cheval_clicks     = []
            st.rerun()

        if st.button("⏭️ Passer (sans annotation)", use_container_width=True, key=f"cheval_skip_{pos}"):
            save_annotation(
                sheet_id, sheet_name, current["sheet_idx"],
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

    if st.session_state.cheval_step == "annotate" and "cheval_queue" in st.session_state:
        _show_annotate(sheet_id, sheet_name)
    else:
        st.session_state.cheval_step = "upload"
        _show_upload(sheet_id, sheet_name, bucket_name)
