from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import requests, csv, time, tempfile, json, os
from datetime import datetime, timezone, timedelta
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="Template")

API_URL = "https://api.videosdk.live/v2/sessions/"
PER_PAGE = 20
DELAY_SEC = 0.2


def month_start_end_epoch_ms(year: int, month: int):
    start_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    next_month_dt = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    end_dt = next_month_dt - timedelta(seconds=1)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok"}


def fetch_sessions(api_key: str, year: int, month: int):
    start_ms, end_ms = month_start_end_epoch_ms(year, month)
    all_sessions = []
    page = 1

    while True:
        payload = {"page": page, "perPage": PER_PAGE, "startDate": start_ms, "endDate": end_ms}
        headers = {"Authorization": api_key}

        resp = requests.get(API_URL, headers=headers, params=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        all_sessions.extend(data.get("data", []))

        page_info = data.get("pageInfo", {})
        if page_info.get("currentPage", 1) >= page_info.get("lastPage", 1):
            break

        page += 1
        time.sleep(DELAY_SEC)

    if not all_sessions:
        raise HTTPException(status_code=404, detail="No sessions found")

    return all_sessions


# ---------------------- SIMPLE FETCH ----------------------
@app.get("/fetch")
def fetch(api_key: str, year: int, month: int):
    sessions = fetch_sessions(api_key, year, month)
    return {"count": len(sessions), "sessions": sessions}


# ---------------------- SIMPLE CSV GENERATION ----------------------
@app.get("/generate-csv")
def generate_csv(api_key: str, year: int, month: int, participant_columns: int = None):
    all_sessions = fetch_sessions(api_key, year, month)

    actual_max_participants = max(len(sess.get("participants", [])) for sess in all_sessions)
    desired_max = actual_max_participants if not participant_columns else max(int(participant_columns), 0)

    # CSV headers
    headers = [
        "session_id", "room_id", "session_start_time",
        "session_end_time", "status", "number_of_participants"
    ]

    for i in range(1, desired_max + 1):
        headers.extend([
            f"participant{i}_id",
            f"participant{i}_name",
            f"participant{i}_first_start",
            f"participant{i}_last_end"
        ])

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")

    with open(tmp.name, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

        for sess in all_sessions:
            row = {
                "session_id": sess.get("id", ""),
                "room_id": sess.get("roomId", ""),
                "session_start_time": sess.get("start", ""),
                "session_end_time": sess.get("end", ""),
                "status": sess.get("status", ""),
                "number_of_participants": len(sess.get("participants", []))
            }

            participants = sess.get("participants", [])
            for idx, p in enumerate(participants[:desired_max], start=1):
                logs = p.get("timelog", [])
                starts = [t.get("start") for t in logs if t.get("start")]
                ends = [t.get("end") for t in logs if t.get("end")]
                row[f"participant{idx}_id"] = p.get("participantId", "")
                row[f"participant{idx}_name"] = p.get("name", "")
                row[f"participant{idx}_first_start"] = min(starts) if starts else ""
                row[f"participant{idx}_last_end"] = max(ends) if ends else ""

            # Fill empty columns
            for idx in range(len(participants)+1, desired_max+1):
                row[f"participant{idx}_id"] = ""
                row[f"participant{idx}_name"] = ""
                row[f"participant{idx}_first_start"] = ""
                row[f"participant{idx}_last_end"] = ""

            writer.writerow(row)

    filename = f"usage_{year}_{month}.csv"
    return FileResponse(tmp.name, filename=filename, media_type="text/csv")
