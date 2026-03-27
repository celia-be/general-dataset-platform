"""
Delara Vision — Annotation Platform
=====================================
Single-file entry point with portal routing.

Architecture
------------
• One password per module (configured in .streamlit/secrets.toml)
• Session state tracks authentication + current module
• No Streamlit multi-page sidebar — everything routes through this file
• Modules are imported as Python functions → clean separation of concerns
"""

import streamlit as st

# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title="Delara Annotation Platform",
    #page_icon="🐾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global dark theme (matches original app.py) ──────────────────────────────
st.markdown(
    """
    <style>
    html, body, .stApp,
    [data-testid="stAppViewContainer"] {
        background-color: #000000 !important;
    }
    [data-testid="stHeader"],
    [data-testid="stToolbar"] {
        background: transparent !important;
    }
    /* Hide sidebar nav entirely */
    [data-testid="stSidebarNav"],
    section[data-testid="stSidebar"] {
        display: none !important;
    }
    /* Text */
    h1, h2, h3, h4, h5, h6,
    p, span, label, li, div {
        color: #F5F5F5 !important;
    }
    /* Code blocks */
    pre, code {
        background-color: #111111 !important;
        color: #F5F5F5 !important;
        border-radius: 8px !important;
    }
    /* Alerts */
    .stAlert {
        background-color: #111111 !important;
        border: 1px solid #333333 !important;
        border-radius: 8px !important;
    }
    /* Progress bar */
    [data-testid="stProgressBar"] > div          { background-color: #222 !important; }
    [data-testid="stProgressBar"] > div > div    { background: #4F8BF9 !important; }
    /* Buttons — primary */
    .stButton > button {
        background-color: #4F8BF9 !important;
        color: #FFFFFF !important;
        border-radius: 999px !important;
        border: 1px solid #4F8BF9 !important;
        padding: 0.3rem 1.2rem !important;
        font-weight: 500 !important;
    }
    .stButton > button:hover {
        background-color: #6C9DFF !important;
        border-color: #6C9DFF !important;
    }
    /* Input fields */
    input, textarea, select {
        background-color: #111111 !important;
        color: #F5F5F5 !important;
        border-color: #333333 !important;
    }
    /* Module cards */
    .module-card {
        background: #111111;
        border: 1px solid #2a2a2a;
        border-radius: 16px;
        padding: 28px 24px;
        text-align: center;
        height: 100%;
    }
    .module-card:hover {
        border-color: #4F8BF9;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state init ───────────────────────────────────────────────────────
if "module" not in st.session_state:
    st.session_state.module = None
if "auth" not in st.session_state:
    st.session_state.auth = {}

# ── Router ───────────────────────────────────────────────────────────────────
module = st.session_state.module

if module == "horse" and st.session_state.auth.get("horse"):
    from modules.horse_old import show
    show()
    st.stop()

elif module == "pets" and st.session_state.auth.get("pets"):
    from modules.pets import show
    show()
    st.stop()

elif module == "data" and st.session_state.auth.get("data"):
    from modules.data import show
    show()
    st.stop()

# ── Portal page ──────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="text-align:center; padding: 40px 0 8px 0;">
        <span style="font-size:48px">🐾</span>
        <h1 style="font-size:2.4rem; font-weight:700; margin:8px 0 4px 0;">
            Delara Annotation Platform
        </h1>
        <p style="color:#888; font-size:1rem; margin:0;">
            Select your module and enter your password to continue
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.markdown("<br>", unsafe_allow_html=True)

# ── Three module cards ────────────────────────────────────────────────────────
MODULES = [
    {
        "key":   "horse",
        "icon":  "🐴",
        "title": "Horse Annotation",
        "desc":  "X-ray radiograph labelling\n(zone · view · limb)",
        "color": "#1a3a5c",
    },
    {
        "key":   "pets",
        "icon":  "🐾",
        "title": "Pets Annotations",
        "desc":  "Species · body part · view\n+ bounding boxes",
        "color": "#1a3a2a",
    },
    {
        "key":   "data",
        "icon":  "📊",
        "title": "Data Validation",
        "desc":  "Label validation\n+ anomaly bounding boxes",
        "color": "#2a1a3a",
    },
]

cols = st.columns(3, gap="large")

for col, mod in zip(cols, MODULES):
    with col:
        # Card header
        st.markdown(
            f"""
            <div class="module-card" style="border-top: 4px solid {mod['color'].replace('#1a', '#4')
                                             .replace('#2a', '#6')}99;">
                <div style="font-size:52px; line-height:1.2">{mod['icon']}</div>
                <h3 style="margin:12px 0 6px 0; font-size:1.2rem">{mod['title']}</h3>
                <p style="color:#888; font-size:0.85rem; white-space:pre-line; margin:0 0 16px 0">
                    {mod['desc']}
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("<br>", unsafe_allow_html=True)

        key = mod["key"]

        # Password input + enter button
        pwd = st.text_input(
            "Password",
            type="password",
            key=f"pwd_{key}",
            label_visibility="collapsed",
            placeholder="🔑 Enter password…",
        )

        if st.button(f"Enter  →", key=f"btn_{key}", use_container_width=True):
            expected = st.secrets.get("passwords", {}).get(key, "")
            if pwd and pwd == expected:
                st.session_state.auth[key] = True
                st.session_state.module = key
                st.rerun()
            elif not pwd:
                st.warning("Please enter a password.")
            else:
                st.error("Incorrect password.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<br><br>", unsafe_allow_html=True)
st.markdown(
    "<p style='text-align:center; color:#444; font-size:0.8rem'>"
    "Delara Vision · Annotation Platform · All data stored securely in Google Drive &amp; Sheets"
    "</p>",
    unsafe_allow_html=True,
)
