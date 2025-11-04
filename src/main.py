from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import requests, csv, time, tempfile, json, os, uuid
from datetime import datetime, timezone, timedelta
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
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

# Store generated CSV files temporarily (in production, use proper storage)
csv_files = {}


def month_start_end_epoch_ms(year: int, month: int):
    start_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        next_month_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_dt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    end_dt = next_month_dt - timedelta(seconds=1)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Simple HTML form UI"""
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
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        resp = requests.get(API_URL, headers=headers, params=payload)

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        all_sessions.extend(data.get("data", []))
        page_info = data.get("pageInfo", {})

        if page_info.get("currentPage", page) >= page_info.get("lastPage", page):
            break
        page += 1
        time.sleep(DELAY_SEC)

    if not all_sessions:
        raise HTTPException(status_code=404, detail="No sessions found for given month/year")

    return all_sessions

@app.get("/fetch")
def fetch(api_key: str, year: int, month: int):
    sessions = fetch_sessions(api_key, year, month)
    return JSONResponse({"count": len(sessions), "sessions": sessions})

@app.get("/fetch-stream")
def fetch_stream(api_key: str, year: int, month: int):
    def event_gen():
        start_ms, end_ms = month_start_end_epoch_ms(year, month)
        all_sessions = []
        page = 1
        last_page = None
        while True:
            payload = {"page": page, "perPage": PER_PAGE, "startDate": start_ms, "endDate": end_ms}
            headers = {"Authorization": api_key, "Content-Type": "application/json"}
            resp = requests.get(API_URL, headers=headers, params=payload)
            if resp.status_code != 200:
                err = {"type": "error", "status": resp.status_code, "detail": resp.text}
                yield f"data: {json.dumps(err)}\n\n"
                return
            data = resp.json()
            all_sessions.extend(data.get("data", []))
            page_info = data.get("pageInfo", {})
            if last_page is None:
                last_page = page_info.get("lastPage", 1) or 1
                init = {"type": "init", "lastPage": last_page}
                yield f"data: {json.dumps(init)}\n\n"
            current = page_info.get("currentPage", page)
            prog = {"type": "progress", "currentPage": current, "lastPage": last_page}
            yield f"data: {json.dumps(prog)}\n\n"
            if current >= last_page:
                complete = {"type": "complete", "count": len(all_sessions), "sessions": all_sessions}
                yield f"data: {json.dumps(complete)}\n\n"
                return
            page += 1
            time.sleep(DELAY_SEC)
    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/generate-csv")
def generate_csv(api_key: str, year: int, month: int, participant_columns: int = None):
    all_sessions = fetch_sessions(api_key, year, month)

    actual_max_participants = max(len(sess.get("participants", [])) for sess in all_sessions)
    # If user sets participant_columns, use it; otherwise use actual maximum.
    # Allow requesting more columns than actual; those will be empty in rows.
    desired_max = actual_max_participants if participant_columns in (None, 0) else max(0, int(participant_columns))
    csv_headers = ["session_id", "room_id", "session_start_time", "session_end_time", "status", "number_of_participants"]
    for i in range(1, desired_max + 1):
        csv_headers.extend([
            f"participant{i}_id", f"participant{i}_name",
            f"participant{i}_first_start", f"participant{i}_last_end"
        ])

    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    with open(tmpfile.name, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers)
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
                timelog = p.get("timelog", [])
                first_start, last_end = "", ""
                if timelog:
                    starts = [t.get("start") for t in timelog if t.get("start")]
                    ends = [t.get("end") for t in timelog if t.get("end")]
                    if starts: first_start = min(starts)
                    if ends: last_end = max(ends)

                row[f"participant{idx}_id"] = p.get("participantId", "")
                row[f"participant{idx}_name"] = p.get("name", "")
                row[f"participant{idx}_first_start"] = first_start
                row[f"participant{idx}_last_end"] = last_end

            for idx in range(len(participants[:desired_max]) + 1, desired_max + 1):
                row[f"participant{idx}_id"] = ""
                row[f"participant{idx}_name"] = ""
                row[f"participant{idx}_first_start"] = ""
                row[f"participant{idx}_last_end"] = ""

            writer.writerow(row)

    filename = f"usage_{year}_{month}.csv"
    return FileResponse(tmpfile.name, filename=filename, media_type="text/csv")

@app.get("/generate-csv-stream")
def generate_csv_stream(api_key: str, year: int, month: int, participant_columns: int = None):
    def event_gen():
        try:
            yield f"data: {json.dumps({'type': 'init', 'message': 'Fetching sessions...'})}\n\n"
            all_sessions = fetch_sessions(api_key, year, month)
            total = len(all_sessions)
            yield f"data: {json.dumps({'type': 'progress', 'step': 'fetch', 'message': f'Fetched {total} sessions', 'progress': 0, 'total': total})}\n\n"

            actual_max_participants = max(len(sess.get("participants", [])) for sess in all_sessions)
            desired_max = actual_max_participants if participant_columns in (None, 0) else max(0, int(participant_columns))
            csv_headers = ["session_id", "room_id", "session_start_time", "session_end_time", "status", "number_of_participants"]
            for i in range(1, desired_max + 1):
                csv_headers.extend([
                    f"participant{i}_id", f"participant{i}_name",
                    f"participant{i}_first_start", f"participant{i}_last_end"
                ])

            file_id = str(uuid.uuid4())
            tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
            csv_files[file_id] = tmpfile.name

            with open(tmpfile.name, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_headers)
                writer.writeheader()

                for idx, sess in enumerate(all_sessions, start=1):
                    row = {
                        "session_id": sess.get("id", ""),
                        "room_id": sess.get("roomId", ""),
                        "session_start_time": sess.get("start", ""),
                        "session_end_time": sess.get("end", ""),
                        "status": sess.get("status", ""),
                        "number_of_participants": len(sess.get("participants", []))
                    }
                    participants = sess.get("participants", [])
                    for p_idx, p in enumerate(participants[:desired_max], start=1):
                        timelog = p.get("timelog", [])
                        first_start, last_end = "", ""
                        if timelog:
                            starts = [t.get("start") for t in timelog if t.get("start")]
                            ends = [t.get("end") for t in timelog if t.get("end")]
                            if starts: first_start = min(starts)
                            if ends: last_end = max(ends)

                        row[f"participant{p_idx}_id"] = p.get("participantId", "")
                        row[f"participant{p_idx}_name"] = p.get("name", "")
                        row[f"participant{p_idx}_first_start"] = first_start
                        row[f"participant{p_idx}_last_end"] = last_end

                    for p_idx in range(len(participants[:desired_max]) + 1, desired_max + 1):
                        row[f"participant{p_idx}_id"] = ""
                        row[f"participant{p_idx}_name"] = ""
                        row[f"participant{p_idx}_first_start"] = ""
                        row[f"participant{p_idx}_last_end"] = ""

                    writer.writerow(row)
                    if idx % 10 == 0 or idx == total:
                        yield f"data: {json.dumps({'type': 'progress', 'step': 'generate', 'message': f'Processing session {idx}/{total}', 'progress': idx, 'total': total})}\n\n"

            filename = f"usage_{year}_{month}.csv"
            complete = {"type": "complete", "fileId": file_id, "filename": filename}
            yield f"data: {json.dumps(complete)}\n\n"
        except Exception as e:
            err = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(err)}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream")

@app.get("/download-csv/{file_id}")
def download_csv(file_id: str):
    if file_id not in csv_files:
        raise HTTPException(status_code=404, detail="File not found")
    filepath = csv_files[file_id]
    filename = os.path.basename(filepath)
    return FileResponse(filepath, filename=filename, media_type="text/csv")
