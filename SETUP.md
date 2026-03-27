# Delara Annotation Platform — Setup Guide

## Project structure

```
annotation-platform/
├── streamlit_app.py          ← entry point / portal
├── modules/
│   ├── horse.py              ← Horse X-Ray annotation
│   ├── pets.py               ← Pets annotation (labels + bbox)
│   └── data.py               ← Data validation (migrated from app.py)
├── utils/
│   ├── google_drive.py       ← image loading from Drive
│   └── google_sheets.py      ← read/write + progress tracking
├── .streamlit/
│   ├── secrets.toml          ← YOUR secrets (gitignored, never commit)
│   └── secrets.toml.example  ← template to fill in
└── requirements.txt
```

Foncionnement:
GCP console.cloud.google.com
  └── Crée un "service account" (email robot + clé JSON)
  └── Active l'API Google Drive
  └── Active l'API Google Sheets

Votre compte Google normal (drive.google.com)
  └── Vous partagez votre dossier Drive avec l'email du robot → il peut lire les images
  └── Vous partagez votre Sheet avec l'email du robot → il peut lire/écrire les annotations

Votre app Streamlit
  └── Utilise la clé JSON pour s'authentifier comme le robot
  └── Lit les images depuis Drive
  └── Écrit les annotations dans Sheets
Ensuite:
Vous (compte Workspace)
  └── Ouvrez votre Google Sheet → Partager → collez l'email du robot → Éditeur
  └── Ouvrez votre Google Drive → Partager → collez l'email du robot → Lecteur

Streamlit (avec la clé JSON du robot)
  └── S'authentifie comme le robot
  └── Le robot voit le Sheet et le dossier Drive car vous les avez partagés avec lui
  └── Lit les images, écrit les annotations — exactement comme si c'était vous
---

## Step 1 — Create a Google Cloud service account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable these two APIs:
   - **Google Drive API**
   - **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Give it a name (e.g. `annotation-bot`)
6. Click **Create and Continue** (no roles needed at project level)
7. Click on the new account → **Keys → Add Key → Create new key → JSON**
8. Download the JSON file — you will paste its content into `secrets.toml`

---

## Step 2 — Create Google Sheets (one per module)

Create **3 separate Google Sheets** (or 3 tabs in one Sheet).

### Horse Sheet — required columns (exact header names):
```
image_id | image_name | report_id | report_name | membre | zone | vue | manual_label | status | annotated_at
```
- `image_id`: Google Drive file ID of the X-ray image
- `report_id`: Google Drive file ID of the PDF report (can be empty)
- `status`: set to `pending` for all rows initially

### Pets Sheet — required columns:
```
image_id | image_name | species | body_part | view | confirmed_label | bbox | status | annotated_at
```

### Data Sheet — required columns:
```
image_id | image_name | proposed_label | report_description | confirmed_label | bbox | status | annotated_at
```

**Share each Sheet** with the service account email (e.g. `annotation-bot@your-project.iam.gserviceaccount.com`) as **Editor**.

---

## Step 3 — Upload images to Google Drive

1. Create a folder in Google Drive for each dataset
2. Upload your images (PNG/JPG)
3. **Share the folder** with the service account email as **Viewer**
4. For each image, copy its **file ID** from the URL:
   `https://drive.google.com/file/d/`**`THIS_IS_THE_FILE_ID`**`/view`
5. Paste the file IDs into the `image_id` column of the corresponding Sheet

> **Tip for bulk import:** Use [Google Apps Script](https://script.google.com) or
> the Python script below to list all file IDs in a folder automatically.

```python
# list_drive_files.py — run once to populate your Sheet
from google.oauth2 import service_account
from googleapiclient.discovery import build

CREDS_FILE = "your-service-account.json"   # path to your JSON key
FOLDER_ID  = "your_drive_folder_id"

creds   = service_account.Credentials.from_service_account_file(CREDS_FILE,
            scopes=["https://www.googleapis.com/auth/drive.readonly"])
service = build("drive", "v3", credentials=creds)

results = service.files().list(
    q=f"'{FOLDER_ID}' in parents and trashed=false",
    fields="files(id, name)",
    pageSize=1000,
).execute()

for f in results["files"]:
    print(f["id"], f["name"])   # paste these into your Sheet
```

---

## Step 4 — Configure secrets.toml

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `secrets.toml`:
- Fill `[passwords]` with a password per module
- Paste the full JSON service account content into `[gcp_service_account]`
- Fill `[sheets]` with the Spreadsheet IDs and sheet tab names

**Add to .gitignore:**
```
.streamlit/secrets.toml
```

---

## Step 5 — Deploy on Railway (recommended — no sleep, private repo)

1. Push this folder as a **private GitHub repo**
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Railway auto-detects Streamlit — set the **start command** to:
   ```
   streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0
   ```
5. In Railway → Variables, add all your secrets from `secrets.toml`
   (Railway has a built-in secrets manager — paste each key individually)
6. Your app gets a permanent URL with no sleep

### Alternative: Streamlit Community Cloud (if you prefer)
1. Push to a **public** GitHub repo (images are NOT in the repo — they're in Drive ✓)
2. Deploy at [share.streamlit.io](https://share.streamlit.io)
3. Add secrets in the Streamlit Cloud secrets UI
4. Use [UptimeRobot](https://uptimerobot.com) (free) to ping every 5 min → no sleep

---

## Step 6 — Share with annotators

Send each annotator:
- The app URL
- Their module password
- Nothing else — no GitHub access, no Drive access, no installations

---

## Progress tracking

Progress is stored in the `status` column of each Sheet:
- `pending` → not yet annotated
- `done` → annotated (with timestamp in `annotated_at`)

The app always resumes from the **first `pending` row**, so annotators can
close the tab and return at any time without losing progress.

To reset an annotation, simply change `status` back to `pending` in the Sheet.
