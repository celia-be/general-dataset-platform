"""
Cheval Upload — Horse X-Ray Upload & Annotation module.
"""

import io
import json
import time
from datetime import datetime

import streamlit as st
from PIL import Image, ImageDraw

from utils.google_sheets import append_row_to_sheet, save_annotation
from streamlit_image_zoom import image_zoom

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

# ── Constants ─────────────────────────────────────────────────────────────────

THUMB_MAX       = 500
BBOX_SIZE       = 15
ANON_CROP_RATIO = 0.08


# ── GCS upload — fully self-contained, no shared state ───────────────────────

def _gcs_upload(img: Image.Image, filename: str, bucket_name: str) -> str:
    """
    Upload one PIL image to GCS.

    Completely self-contained: creates fresh credentials AND a fresh client
    on every single call. No caching, no shared HTTP pool, no external imports
    that could carry state between calls.
    """
    import json as _json
    from google.cloud import storage as gcs_lib
    from google.oauth2 import service_account as sa

    creds = sa.Credentials.from_service_account_info(
        _json.loads(st.secrets["gcp"]["service_account_json"]),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = gcs_lib.Client(credentials=creds, project=creds.project_id)
    bucket = client.bucket(bucket_name)
    blob   = bucket.blob(filename)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    blob.upload_from_string(buf.getvalue(), content_type="image/png")

    return f"gs://{bucket_name}/{filename}"


# ── Image helpers ─────────────────────────────────────────────────────────────

def _anonymize(img: Image.Image) -> Image.Image:
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


# ── UI helpers ────────────────────────────────────────────────────────────────

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


def _inject_hover_zoom():
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


# ── Step 1 : Upload ───────────────────────────────────────────────────────────

def _show_upload(sheet_id: str, sheet_name: str, bucket_name: str):
    st.markdown("### Étape 1 — Chargement des images")
    st.caption("L'anonymisation (suppression des bandes haut/bas) est automatique.")

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

    # ── Read ALL raw bytes into memory BEFORE any network call ────────────────
    # Streamlit UploadedFile objects can become invalid once other I/O starts.
    # Reading everything upfront into plain (name, bytes) tuples guarantees
    # each image is fully in RAM before we touch GCS or Sheets.
    status_text  = st.empty()
    status_text.text("Lecture des fichiers…")
    files_data = []
    for f in uploaded_files:
        files_data.append((f.name, f.read()))   # (filename, raw bytes)

    # ── Process each image independently ─────────────────────────────────────
    queue        = []
    errors       = []
    progress_bar = st.progress(0)

    for i, (name, raw_bytes) in enumerate(files_data):
        status_text.text(f"Upload {i + 1}/{n} : {name}…")

        try:
            # --- Decode & anonymise -------------------------------------------
            img  = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            anon = _anonymize(img)

            ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem     = name.rsplit(".", 1)[0]
            filename = f"{stem}_anon_{ts}_{i:03d}.png"

            # --- GCS upload (fresh client, no shared state) -------------------
            file_id = _gcs_upload(anon, filename, bucket_name)

            # --- Sheets row ---------------------------------------------------
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

            # --- Serialise for session state ----------------------------------
            buf = io.BytesIO()
            anon.save(buf, format="PNG")

            queue.append({
                "image_bytes": buf.getvalue(),
                "file_id":     file_id,
                "file_name":   filename,
                "sheet_idx":   sheet_idx,
            })

        except Exception as exc:
            errors.append(f"❌ {name} : {exc}")

        progress_bar.progress((i + 1) / n)

    if errors:
        for msg in errors:
            st.error(msg)

    if not queue:
        st.error("Aucune image n'a pu être uploadée.")
        return

    ok = len(queue)
    status_text.text(f"✅ {ok}/{n} image{'s' if ok > 1 else ''} uploadée{'s' if ok > 1 else ''} !")

    st.session_state.cheval_queue     = queue
    st.session_state.cheval_queue_pos = 0
    st.session_state.cheval_clicks    = []
    st.session_state.cheval_step      = "annotate"
    st.rerun()


# ── Step 2 : Annotation ───────────────────────────────────────────────────────

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

    col_zoom, col_click, col_right = st.columns([1.3, 1.3, 1.1])
    # Column 1 — zoom on hover
    with col_zoom:
        st.caption("🔍 Hover to zoom")
        if HAS_ZOOM:
            image_zoom(img_display, mode="mousemove", size=500, zoom_factor=3.5)
        else:
            st.image(img_display, use_container_width=True)

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

    with col_right:
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
        st.markdown("---")
    #with col_form:
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
