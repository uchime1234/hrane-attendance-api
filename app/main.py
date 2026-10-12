"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from typing import Optional
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP
from pymongo import ReturnDocument 
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient


load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    # R1: truncate to whole seconds before any calculation
    punch_in = punch_in.replace(microsecond=0)
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    seconds = (punch_in - start).total_seconds()
    # R2: strictly more than 10 minutes (600 seconds) is late
    if seconds > 600:
        return int(seconds // 60)
    return 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    punch_in = punch_in.replace(microsecond=0) # set the micrseconds to 0, so there are no rounding issues when calculating the difference between punch_in and punch_out
    punch_out = punch_out.replace(microsecond=0) # did the same thing
    seconds = int((punch_out - punch_in).total_seconds()) # calculate the difference between the two times and then covert the answer to seconds
    hours = Decimal(seconds) / Decimal(3600) # convert the seconds to hours  and then what every answer change it to a decimal 
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) # finally return the hours as a float, rounded to 2 decimal places using the ROUND_HALF_UP method



def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST) # so takes the hours from the shift_end and then convert it to a datetime object(make it the data along with the time) and then set the timezone to IST
    if shift_end <= shift_start:
        end = end + timedelta(days=1)
    punch_out = punch_out.replace(microsecond=0)
    extra_minutes = int((punch_out - end).total_seconds() / 60)
    if extra_minutes >= 30:
        return extra_minutes
    return 0

    
# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.on_event("startup")
def on_startup():
    ensure_indexes()

def ensure_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)], unique=True
    )
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("status", 1), ("date", 1)])


def record_to_api(doc: dict) -> dict:
    """Convert a MongoDB attendance document to the API shape:
    drop _id, convert BSON datetimes to epoch milliseconds."""
    out = dict(doc)
    out.pop("_id", None)

    # punch_in / punch_out -> epoch ms or None
    for key in ("punch_in", "punch_out"):
        v = out.get(key)
        out[key] = int(v.timestamp() * 1000) if v else None

    # history entries: convert `at` and any datetime values in `changes`
    history = out.get("history") or []
    new_history = []
    for entry in history:
        entry = dict(entry)
        if entry.get("at"):
            entry["at"] = int(entry["at"].timestamp() * 1000)
        changes = entry.get("changes") or {}
        new_changes = {}
        for field, pair in changes.items():
            pair = dict(pair)
            for side in ("from", "to"):
                val = pair.get(side)
                if isinstance(val, datetime):
                    pair[side] = int(val.timestamp() * 1000)
            new_changes[field] = pair
        entry["changes"] = new_changes
        new_history.append(entry)
    out["history"] = new_history

    # legacy records may not have half_day
    out.setdefault("half_day", False)

    return out

def employee_to_api(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    if out.get("created_at"):
        out["created_at"] = int(out["created_at"].timestamp() * 1000)
    return out



@app.get("/health") # fixed the endpoint to give proper respond, if the database isnt giving response, it will give 503 error
def health():
    try:
        db.command("ping")
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unreachable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    # Validate: shift_start must differ from shift_end (contract)
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start must differ from shift_end")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)

    # Race-safe: rely on the unique index on emp_code
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    return employee_to_api(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size              # ← fixed pagination
    total = db.employees.count_documents(q)    # ← fixed total
    
    items = list(
        db.employees.find(q)                   # no {"_id": 0} — employee_to_api strips it
        .sort("emp_code", 1)                   # ← added: contract requires this sort
        .skip(skip)
        .limit(page_size)
    )
    return {
        "items": [employee_to_api(d) for d in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # 1. Look up the employee, 404 if not found
    emp = db.employees.find_one({"emp_code": body.emp_code}) 
    if not emp:
        raise HTTPException(404, "employee not found")

    # 2. Build the punch-in moment in UTC, truncated to whole seconds (R1)
    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0) # either false nor true we will still set the microsecond to 0, so that we can avoid any rounding issues when calculating the difference between punch_in and punch_out

    # 3. The attendance date is the IST calendar date of the punch-in (R1)
    d = ts.astimezone(IST).date().isoformat() 

    # 4. Build the document
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    # 5. Race-safe insert: rely on the unique index on (emp_code, date)
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    # 6. Return the API shape (drop _id, convert datetimes to epoch ms)
    return record_to_api(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutRequest):
    # 1. Employee must exist
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    # 2. Build the punch-out moment in UTC, truncated to seconds (R1)
    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    ts = ts.replace(microsecond=0)

    # 3. Find the most recent OPEN record (punch_in <= ts, no punch_out yet)
    #    Sort by date desc so we pick the latest — this is what makes
    #    overnight shifts work: the record's `date` is yesterday, but
    #    punch_in <= ts and punch_out is still None.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": ts},
            "punch_out": None,
        },
        sort=[("date", -1)],
    )
    if not record:
        raise HTTPException(404, "no open punch-in found")

    # 4. Validate the window: punch_out must be after punch_in, within 24h
    punch_in = record["punch_in"]  # this is timezone-aware thanks to tz_aware=True
    if ts <= punch_in:
        raise HTTPException(422, "punched_at must be after punch_in")
    if (ts - punch_in) > timedelta(hours=24):
        raise HTTPException(422, "punched_at more than 24 hours after punch_in")

    # 5. Compute derived fields from the final times
    late_minutes = compute_late_minutes(punch_in, emp["shift_start"])
    work_hours = compute_work_hours(punch_in, ts)
    overtime_minutes = compute_overtime(
        ts, emp["shift_start"], emp["shift_end"], record["date"]
    )
    half_day = work_hours < 4.50  # R5: uses the ROUNDED work_hours

    # 6. Atomic update: only succeeds if the record is STILL open.
    #    Two simultaneous punch-outs: one updates, the other's filter
    #    (punch_out: None) no longer matches → returns None → 409.
    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": body.emp_code,
            "date": record["date"],
            "punch_out": None,  # ← the race guard
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")

    # 7. Return the API shape
    return record_to_api(updated)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    # Validate the date range before touching the DB (R10-style 422)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")

    # Build the filter
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    # Count the filtered set separately (not the whole collection)
    total = db.attendance_logs.count_documents(q)

    # Push sort, skip and limit into MongoDB
    skip = (page - 1) * page_size
    cursor = (
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    # Convert each doc to the API shape (drop _id, datetimes -> epoch ms)
    items = [record_to_api(d) for d in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
