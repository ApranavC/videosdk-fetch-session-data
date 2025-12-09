from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import requests, csv, time, tempfile, uuid, threading
from datetime import datetime, timezone, timedelta
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any

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

# Store job progress and results
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

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
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok"}


def fetch_sessions(api_key: str, year: int, month: int, job_id: str = None):
    start_ms, end_ms = month_start_end_epoch_ms(year, month)
    all_sessions = []
    page = 1
    last_page = None

    while True:
        if job_id:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["current_page"] = page
                    if last_page:
                        jobs[job_id]["progress"] = int((page / last_page) * 100)

        payload = {"page": page, "perPage": PER_PAGE, "startDate": start_ms, "endDate": end_ms}
        headers = {"Authorization": api_key}

        resp = requests.get(API_URL, headers=headers, params=payload)
        if resp.status_code != 200:
            if job_id:
                with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["status"] = "error"
                        jobs[job_id]["error"] = resp.text
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        all_sessions.extend(data.get("data", []))
        page_info = data.get("pageInfo", {})
        
        if last_page is None:
            last_page = page_info.get("lastPage", 1) or 1
            if job_id:
                with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["total_pages"] = last_page

        if page_info.get("currentPage", page) >= last_page:
            break

        page += 1
        time.sleep(DELAY_SEC)

    if not all_sessions:
        if job_id:
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["status"] = "error"
                    jobs[job_id]["error"] = "No sessions found for given month/year"
        raise HTTPException(status_code=404, detail="No sessions found for given month/year")

    return all_sessions

def fetch_sessions_background(job_id: str, api_key: str, year: int, month: int):
    """Background task to fetch sessions"""
    try:
        with jobs_lock:
            jobs[job_id] = {
                "status": "running",
                "progress": 0,
                "current_page": 0,
                "total_pages": 1,
                "message": "Starting fetch..."
            }
        
        sessions = fetch_sessions(api_key, year, month, job_id)
        
        with jobs_lock:
            jobs[job_id] = {
                "status": "completed",
                "progress": 100,
                "result": {
                    "count": len(sessions),
                    "sessions": sessions
                }
            }
    except Exception as e:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)

@app.get("/fetch")
def fetch(background_tasks: BackgroundTasks, api_key: str, year: int, month: int):
    """Start fetch job and return job ID"""
    job_id = str(uuid.uuid4())
    background_tasks.add_task(fetch_sessions_background, job_id, api_key, year, month)
    return JSONResponse({"job_id": job_id})

@app.get("/fetch-status/{job_id}")
def fetch_status(job_id: str):
    """Get status of fetch job"""
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = jobs[job_id].copy()
    
    if job["status"] == "completed":
        # Clean up after returning result
        result = job.pop("result")
        with jobs_lock:
            del jobs[job_id]
        return JSONResponse({"status": "completed", **result})
    
    return JSONResponse(job)


def generate_csv_background(job_id: str, api_key: str, year: int, month: int, participant_columns: int = None):
    """Background task to generate CSV"""
    try:
        with jobs_lock:
            jobs[job_id] = {
                "status": "running",
                "progress": 0,
                "message": "Fetching sessions...",
                "step": "fetch"
            }
        
        all_sessions = fetch_sessions(api_key, year, month, job_id)
        
        with jobs_lock:
            jobs[job_id]["progress"] = 50
            jobs[job_id]["message"] = f"Processing {len(all_sessions)} sessions..."
            jobs[job_id]["step"] = "generate"
            jobs[job_id]["total_sessions"] = len(all_sessions)
            jobs[job_id]["processed_sessions"] = 0

        actual_max_participants = max(len(sess.get("participants", [])) for sess in all_sessions)
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

            for idx, sess in enumerate(all_sessions, 1):
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
                
                # Update progress every 10 sessions or on last session
                if idx % 10 == 0 or idx == len(all_sessions):
                    with jobs_lock:
                        if job_id in jobs:
                            jobs[job_id]["processed_sessions"] = idx
                            jobs[job_id]["progress"] = 50 + int((idx / len(all_sessions)) * 50)

        filename = f"usage_{year}_{month}.csv"
        
        with jobs_lock:
            jobs[job_id] = {
                "status": "completed",
                "progress": 100,
                "file_path": tmpfile.name,
                "filename": filename
            }
    except Exception as e:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(e)

@app.get("/generate-csv")
def generate_csv(background_tasks: BackgroundTasks, api_key: str, year: int, month: int, participant_columns: int = None):
    """Start CSV generation job and return job ID"""
    job_id = str(uuid.uuid4())
    background_tasks.add_task(generate_csv_background, job_id, api_key, year, month, participant_columns)
    return JSONResponse({"job_id": job_id})

@app.get("/csv-status/{job_id}")
def csv_status(job_id: str):
    """Get status of CSV generation job"""
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = jobs[job_id].copy()
    
    if job["status"] == "completed":
        file_path = job.pop("file_path")
        filename = job.pop("filename")
        # Don't delete job yet, keep it for download
        return JSONResponse({"status": "completed", "filename": filename, "job_id": job_id})
    
    return JSONResponse(job)

@app.get("/download-csv/{job_id}")
def download_csv(job_id: str):
    """Download completed CSV file"""
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = jobs[job_id]
        
        if job["status"] != "completed":
            raise HTTPException(status_code=400, detail="CSV not ready yet")
        
        file_path = job["file_path"]
        filename = job["filename"]
        # Clean up job after download
        del jobs[job_id]
    
    return FileResponse(file_path, filename=filename, media_type="text/csv")
