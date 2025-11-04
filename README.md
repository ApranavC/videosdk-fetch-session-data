# Fetch Session Monthly

A minimal FastAPI app that lets you enter a VideoSDK API key, month and year, fetches all sessions for that period, shows them in a table, and allows downloading a CSV.

## Local Run

```bash
cd "/Users/pranav/Desktop/videosdk/APIs & Examples/fetch Session Monthly"

# Optional: create venv
/usr/bin/python3 -m venv .venv
. .venv/bin/activate

pip install -r requirements.txt

# Run on a user port
uvicorn src.main:app --host 0.0.0.0 --port 8888
```

Open `http://localhost:8888`.

If macOS blocks file watching, either omit `--reload` or use:
```bash
WATCHFILES_FORCE_POLLING=true uvicorn src.main:app --host 0.0.0.0 --port 8888 --reload
```

Health check: `GET /health`

## Deploy Free on Render (Recommended)

1. Push your code to GitHub:
   ```bash
   cd "/Users/pranav/Desktop/videosdk"
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

2. Go to https://render.com → Sign up/login (GitHub)

3. Click "New +" → "Blueprint"

4. Connect your GitHub repo

5. Render auto-detects `render.yaml` → Click "Apply" → "Create Blueprint"

6. Wait 5-10 minutes → Your app is live at `https://videosdk-monthly.onrender.com`

**Note:** Free tier spins down after 15 min inactivity (30s wake-up time)

## Deploy Free on Railway

1. Push to GitHub (same as Render step 1)

2. Go to https://railway.app → "New Project" → "Deploy from GitHub repo"

3. Select your repo → Set Root Directory: `APIs & Examples/fetch Session Monthly`

4. Railway auto-detects Python and runs `Procfile`

5. Your app is live at `https://your-app.up.railway.app`

## Deploy Free on PythonAnywhere

1. Sign up at https://www.pythonanywhere.com

2. Open Bash console → Clone repo:
   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd YOUR_REPO/"APIs & Examples/fetch Session Monthly"
   mkvirtualenv videosdk --python=/usr/bin/python3.10
   pip install -r requirements.txt
   ```

3. Web tab → "Add a new web app" → Manual configuration → Python 3.10

4. Edit WSGI file:
   ```python
   import sys
   sys.path.insert(0, '/home/YOUR_USERNAME/YOUR_REPO/APIs & Examples/fetch Session Monthly')
   from src.main import app as application
   ```

5. Reload web app

## Deploy Free on Fly.io

1. Install Fly CLI: `curl -L https://fly.io/install.sh | sh`

2. Push to GitHub

3. Deploy:
   ```bash
   cd "APIs & Examples/fetch Session Monthly"
   fly launch
   fly deploy
   ```

## Endpoints
- `GET /` UI with form to enter API key, month, year; displays sessions and provides CSV link
- `GET /fetch?api_key=...&year=YYYY&month=M` returns JSON `{ count, sessions }`
- `GET /fetch-stream?api_key=...&year=YYYY&month=M` SSE stream with progress
- `GET /generate-csv?api_key=...&year=YYYY&month=M` downloads CSV
- `GET /generate-csv-stream?api_key=...&year=YYYY&month=M` SSE stream with CSV generation progress
- `GET /health` returns `{ "status": "ok" }`
