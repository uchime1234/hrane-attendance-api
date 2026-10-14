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
from fastapi import Path, Query
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


class RegularizeRequest(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.on_event("startup")
def on_startup():
    ensure_indexes()

def _to_ms(v):
    if v is None:
        return None
    return int(v.timestamp() * 1000)

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

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance( emp_code: str = Path(...), date: str = Path(...), body: RegularizeRequest = ..., ):
    # ---- 1. Find the record --------------------------------------------
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        # 404 if the employee doesn't exist OR has no record for that date
        raise HTTPException(404, "no attendance record for that employee and date")

    # Also verify the employee exists (defensive; the record should imply it)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    # ---- 2. Build the "merged result" ----------------------------------
    # Start from the current record; override only what was supplied.
    original = dict(record)
    merged = {
        "status": body.status if body.status is not None else record.get("status"),
        "punch_in": body.punch_in if body.punch_in is not None else _to_ms(record.get("punch_in")),
        "punch_out": body.punch_out if body.punch_out is not None else _to_ms(record.get("punch_out")),
    }

    # ---- 3. Validate the merged result ---------------------------------
    # Normalise status first (in case the client didn't send one, use existing)
    merged_status = merged["status"]

    if merged_status in ("ABSENT", "LEAVE"):
        # These statuses must have NO punch times
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")
        merged["punch_in"] = None
        merged["punch_out"] = None
    else:
        # Presence statuses must have a punch_in
        if merged["punch_in"] is None:
            raise HTTPException(422, "presence status requires a punch_in")

        # punch_in must still fall on the record's attendance date (R1, IST)
        new_punch_in_dt = datetime.fromtimestamp(merged["punch_in"] / 1000, tz=timezone.utc)
        new_date = new_punch_in_dt.astimezone(IST).date().isoformat()
        if new_date != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")

        # punch_out, if given, must be after punch_in and within 24h
        if merged["punch_out"] is not None:
            if merged["punch_out"] <= merged["punch_in"]:
                raise HTTPException(422, "punch_out must be after punch_in")
            if (merged["punch_out"] - merged["punch_in"]) > 24 * 3600 * 1000:
                raise HTTPException(422, "punch_out must be within 24 hours of punch_in")

    # ---- 4. Recompute derived fields from the final times --------------
    if merged["punch_in"] is not None:
        pi_dt = datetime.fromtimestamp(merged["punch_in"] / 1000, tz=timezone.utc).replace(microsecond=0)
    else:
        pi_dt = None

    if merged["punch_out"] is not None:
        po_dt = datetime.fromtimestamp(merged["punch_out"] / 1000, tz=timezone.utc).replace(microsecond=0)
    else:
        po_dt = None

    if merged_status in ("ABSENT", "LEAVE"):
        new_late = 0
        new_work = None
        new_ot = 0
        new_half = False
    else:
        new_late = compute_late_minutes(pi_dt, emp["shift_start"])
        if po_dt is not None:
            new_work = compute_work_hours(pi_dt, po_dt)
            new_ot = compute_overtime(po_dt, emp["shift_start"], emp["shift_end"], date)
            new_half = new_work < 4.50
        else:
            new_work = None
            new_ot = 0
            new_half = False

    # ---- 5. Build the history entry ------------------------------------
    # Compare original vs merged. Collect only fields that ACTUALLY changed.
    changes = {}

    def record_change(field, old_val, new_val):
        if old_val != new_val:
            changes[field] = {"from": old_val, "to": new_val}

    # status
    record_change("status", original.get("status"), merged_status)
    # punch_in / punch_out (compare as epoch ms)
    record_change("punch_in", _to_ms(original.get("punch_in")), merged["punch_in"])
    record_change("punch_out", _to_ms(original.get("punch_out")), merged["punch_out"])
    # derived
    record_change("late_minutes", original.get("late_minutes", 0), new_late)
    record_change("work_hours", original.get("work_hours"), new_work)
    record_change("overtime_minutes", original.get("overtime_minutes", 0), new_ot)
    record_change("half_day", original.get("half_day", False), new_half)

    if not changes:
        raise HTTPException(422, "no fields actually changed")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # ---- 6. Atomic update with a concurrency guard ---------------------
    # The filter requires the ORIGINAL values of the fields we're writing.
    # If someone else changed them since we read, the filter won't match
    # and find_one_and_update returns None → 409.
    guard = {
        "emp_code": emp_code,
        "date": date,
        "status": original.get("status"),
        "punch_in": original.get("punch_in"),
        "punch_out": original.get("punch_out"),
    }

    updated = db.attendance_logs.find_one_and_update(
        guard,
        {
            "$set": {
                "status": merged_status,
                "punch_in": pi_dt,
                "punch_out": po_dt,
                "late_minutes": new_late,
                "work_hours": new_work,
                "overtime_minutes": new_ot,
                "half_day": new_half,
            },
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(409, "record was modified concurrently, please retry")

    # ---- 7. Return the API shape ---------------------------------------
    return record_to_api(updated)

from datetime import date as date_cls, timedelta as td_cls


def _month_range(month: str):
    """'2026-07' -> (date(2026,7,1), date(2026,7,31))."""
    year, mon = map(int, month.split("-"))
    first = date_cls(year, mon, 1)
    if mon == 12:
        nxt = date_cls(year + 1, 1, 1)
    else:
        nxt = date_cls(year, mon + 1, 1)
    last = nxt - td_cls(days=1)
    return first, last


def _working_days_between(start: date_cls, end: date_cls) -> int:
    """Mon-Fri count in [start, end]."""
    if start > end:
        return 0
    days = 0
    d = start
    while d <= end:
        if d.weekday() < 5:      # 0=Mon ... 4=Fri
            days += 1
        d += td_cls(days=1)
    return days


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # 1. Employee must exist
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    # 2. Month boundaries + working_days
    first_day, last_day = _month_range(month)
    joined = date_cls.fromisoformat(emp["joined_on"])
    working_from = max(first_day, joined)      # mid-month joiner -> count from joined_on
    working_days = _working_days_between(working_from, last_day)

    month_start_str = first_day.isoformat()
    month_end_str = last_day.isoformat()

    # 3. One aggregation: $match -> $group
    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": month_start_str, "$lte": month_end_str},
        }},
        {"$group": {
            "_id": None,
            # present_days: PRESENT/WFH/ON_DUTY, Mon-Fri only,
            # half_day=true contributes 0.5, else 1.0
            "present_days": {
                "$sum": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$in": [
                                    {"$dayOfWeek": {"$toDate": "$date"}},
                                    [2, 3, 4, 5, 6],   # Mon=2 ... Fri=6
                                ]},
                            ]
                        },
                        {"$cond": [
                            {"$eq": [{"$ifNull": ["$half_day", False]}, True]},
                            0.5,
                            1.0,
                        ]},
                        0,
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [
                    {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0
                ]}
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$late_minutes", 0]}
            },
            "total_overtime_minutes": {
                "$sum": {"$ifNull": ["$overtime_minutes", 0]}
            },
        }},
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    agg = result[0] if result else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0,
    }
    agg.pop("_id", None)

    # 4. attendance_pct (4 decimals half-up), null when working_days == 0
    if working_days == 0:
        pct = None
    else:
        pct_dec = (Decimal(str(agg["present_days"])) / Decimal(working_days)) * 100
        pct = float(pct_dec.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": agg["present_days"],
        "leave_days": agg["leave_days"],
        "late_count": agg["late_count"],
        "total_late_minutes": agg["total_late_minutes"],
        "total_overtime_minutes": agg["total_overtime_minutes"],
        "attendance_pct": pct,
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    # Month range
    first_day, last_day = _month_range(month)
    month_start_str = first_day.isoformat()
    month_end_str = last_day.isoformat()

    # Pipeline over `employees` (headcount includes zero-log employees)
    emp_match = {"joined_on": {"$lte": month_end_str}}
    if department:
        emp_match["department"] = department

    pipeline = [
        {"$match": emp_match},

        # Attach the employee's attendance records for this month
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"ec": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$ec"]},
                    "date": {"$gte": month_start_str, "$lte": month_end_str},
                }},
            ],
            "as": "logs",
        }},

        # Per-employee metrics (from the joined logs)
        {"$project": {
            "emp_code": 1,
            "department": 1,
            "present_days": {
                "$reduce": {
                    "input": "$logs",
                    "initialValue": 0.0,
                    "in": {
                        "$add": [
                            "$$value",
                            {"$cond": [
                                {"$and": [
                                    {"$in": ["$$this.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": [{"$dayOfWeek": {"$toDate": "$$this.date"}}, [2, 3, 4, 5, 6]]},
                                ]},
                                {"$cond": [{"$eq": [{"$ifNull": ["$$this.half_day", False]}, True]}, 0.5, 1.0]},
                                0,
                            ]},
                        ]
                    },
                }
            },
            "late_count": {
                "$reduce": {
                    "input": "$logs",
                    "initialValue": 0,
                    "in": {"$add": ["$$value", {"$cond": [{"$gt": [{"$ifNull": ["$$this.late_minutes", 0]}, 0]}, 1, 0]}]},
                }
            },
            "total_late_minutes": {
                "$reduce": {
                    "input": "$logs",
                    "initialValue": 0,
                    "in": {"$add": ["$$value", {"$ifNull": ["$$this.late_minutes", 0]}]},
                }
            },
            "leave_count": {
                "$reduce": {
                    "input": "$logs",
                    "initialValue": 0,
                    "in": {"$add": ["$$value", {"$cond": [{"$eq": ["$$this.status", "LEAVE"]}, 1, 0]}]},
                }
            },
            "on_duty_count": {
                "$reduce": {
                    "input": "$logs",
                    "initialValue": 0,
                    "in": {"$add": ["$$value", {"$cond": [{"$eq": ["$$this.status", "ON_DUTY"]}, 1, 0]}]},
                }
            },
            # Work hours only for presence-status records that have a work_hours value
            "work_hours_values": {
                "$map": {
                    "input": {
                        "$filter": {
                            "input": "$logs",
                            "as": "l",
                            "cond": {"$and": [
                                {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne": [{"$ifNull": ["$$l.work_hours", None]}, None]},
                            ]},
                        }
                    },
                    "as": "l",
                    "in": "$$l.work_hours",
                }
            },
        }},

        # Department-level rollup
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
            "all_work_hours": {"$push": "$work_hours_values"},
        }},

        {"$sort": {"_id": 1}},
    ]

    rows = list(db.employees.aggregate(pipeline))

    items = []
    for r in rows:
        # Flatten work_hours arrays: all_work_hours is [ [...], [...], ... ]
        flat = [h for arr in r.get("all_work_hours", []) for h in arr]
        if flat:
            avg = Decimal(str(sum(flat))) / Decimal(len(flat))
            avg_wh = float(avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            avg_wh = None

        items.append({
            "department": r["_id"],
            "headcount": r["headcount"],
            "present_days": r["present_days"],
            "avg_work_hours": avg_wh,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"],
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    # Month range
    first_day, last_day = _month_range(month)
    month_start_str = first_day.isoformat()
    month_end_str = last_day.isoformat()

    pipeline = [
        # 1. Only records for this month
        {"$match": {
            "date": {"$gte": month_start_str, "$lte": month_end_str},
        }},

        # 2. Group by emp_code → total late minutes and count of late records
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {
                "$sum": {"$cond": [
                    {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0
                ]}
            },
        }},

        # 3. Only employees with a total > 0
        {"$match": {"total_late_minutes": {"$gt": 0}}},

        # 4. Look up name + department from employees; ignore orphan logs
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp",
        }},
        {"$unwind": "$emp"},   # drops rows where no employee matched

        # 5. Optional department filter
        *([{"$match": {"emp.department": department}}] if department else []),

        # 6. Sort by total desc, then emp_code asc
        {"$sort": {"total_late_minutes": -1, "_id": 1}},

        # 7. Rank with tie handling (1, 2, 2, 4)
                # 7. Rank with tie handling (1, 2, 2, 4)
        #    Note: $rank requires sortBy to have exactly ONE field.
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},

        # 8. Keep only rows whose rank <= limit
        {"$match": {"rank": {"$lte": limit}}},

        # 9. Final sort (same as before, ordering rows for output)
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ]

    rows = list(db.attendance_logs.aggregate(pipeline))

    items = [
        {
            "rank": r["rank"],
            "emp_code": r["_id"],
            "name": r["emp"]["name"],
            "department": r["emp"]["department"],
            "total_late_minutes": r["total_late_minutes"],
            "late_count": r["late_count"],
        }
        for r in rows
    ]

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, **params):
    # Pull `from` and `to` from the query string manually so we can return 422 cleanly
    from_str = params.get("from")
    to_str = params.get("to")
    if not from_str or not to_str:
        raise HTTPException(422, "from and to are required")

    try:
        from_day = date_cls.fromisoformat(from_str)
        to_day = date_cls.fromisoformat(to_str)
    except ValueError:
        raise HTTPException(422, "from and to must be YYYY-MM-DD")

    if to_day < from_day:
        raise HTTPException(422, "to must be >= from")
    if (to_day - from_day).days > 92:
        raise HTTPException(422, "range must be <= 92 days")

    # 404 if the department has no employees at all
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "unknown department")

    # All department employees (for per-day headcount)
    emps = list(db.employees.find(
        {"department": department}, {"_id": 0, "emp_code": 1, "joined_on": 1}
    ))
    emp_codes = [e["emp_code"] for e in emps]
    # joined_on per emp, as date objects
    joined_map = {e["emp_code"]: date_cls.fromisoformat(e["joined_on"]) for e in emps}

    from_str_iso = from_day.isoformat()
    to_str_iso = to_day.isoformat()

    # 1. Pull attendance rows for the department in range (via emp_codes)
    daily_rows = list(db.attendance_logs.aggregate([
        {"$match": {
            "emp_code": {"$in": emp_codes},
            "date": {"$gte": from_str_iso, "$lte": to_str_iso},
        }},
        {"$group": {
            "_id": "$date",
            "present_count": {
                "$sum": {"$cond": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1.0]},
                    0,
                ]}
            },
            "late_count": {
                "$sum": {"$cond": [
                    {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0
                ]}
            },
        }},
    ]))
    # date string -> (present_count, late_count)
    by_date = {r["_id"]: r for r in daily_rows}

    # 2. Build one row per calendar day in range
    items = []
    rates_so_far = []   # non-null rates, in order, for the moving average
    d = from_day
    while d <= to_day:
        d_str = d.isoformat()
        is_working = d.weekday() < 5
        # headcount for that day = employees whose joined_on <= d
        headcount = sum(1 for e in emps if joined_map[e["emp_code"]] <= d)

        row = by_date.get(d_str, {"present_count": 0, "late_count": 0})
        present = float(row["present_count"])
        late = int(row["late_count"])

        if is_working and headcount > 0:
            rate = (Decimal(str(present)) / Decimal(headcount) * 100 / 100)  # fraction, not %
            rate_f = float(rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        else:
            rate_f = None

        if rate_f is not None:
            rates_so_far.append(rate_f)
        # moving average = mean of last <=7 non-null rates
        window = rates_so_far[-7:]
        if window:
            mov = float((Decimal(str(sum(window))) / Decimal(len(window)))
                        .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        else:
            mov = None

        items.append({
            "date": d_str,
            "is_working_day": is_working,
            "headcount": headcount,
            "present_count": present,
            "late_count": late,
            "attendance_rate": rate_f,
            "moving_avg_7d": mov,
        })
        d = d + td_cls(days=1)

    return {"department": department, "items": items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    from_str = from_
    to_str = to

    # Build the pipeline for the requested endpoint
    if endpoint == "attendance_list":
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
        coll = "attendance_logs"
        cursor = (db.attendance_logs.find(q)
                  .sort([("date", -1), ("emp_code", 1)])
                  .skip(0).limit(20))
        plan = cursor.explain()

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month required")
        first_day, last_day = _month_range(month)
        pipeline = [
            {"$match": {
                "emp_code": emp_code,
                "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
            }},
            {"$group": {"_id": None, "n": {"$sum": 1}}},
        ]
        coll = "attendance_logs"
        plan = db.command("explain", {
            "aggregate": coll, "pipeline": pipeline, "cursor": {}
        }, verbosity="executionStats")

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month required")
        first_day, last_day = _month_range(month)
        emp_match = {"joined_on": {"$lte": last_day.isoformat()}}
        if department:
            emp_match["department"] = department
        pipeline = [
            {"$match": emp_match},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"ec": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$ec"]},
                        "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()},
                    }},
                ],
                "as": "logs",
            }},
            {"$group": {"_id": "$department", "n": {"$sum": 1}}},
        ]
        coll = "employees"
        plan = db.command("explain", {
            "aggregate": coll, "pipeline": pipeline, "cursor": {}
        }, verbosity="executionStats")

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month required")
        first_day, last_day = _month_range(month)
        pipeline = [
            {"$match": {"date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()}}},
            {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 0}}},
        ]
        coll = "attendance_logs"
        plan = db.command("explain", {
            "aggregate": coll, "pipeline": pipeline, "cursor": {}
        }, verbosity="executionStats")

    elif endpoint == "department_trend":
        if not department or not from_str or not to_str:
            raise HTTPException(422, "department, from, to required")
        pipeline = [
            {"$match": {
                "date": {"$gte": from_str, "$lte": to_str},
            }},
            {"$group": {"_id": "$date", "n": {"$sum": 1}}},
        ]
        coll = "attendance_logs"
        plan = db.command("explain", {
            "aggregate": coll, "pipeline": pipeline, "cursor": {}
        }, verbosity="executionStats")

    else:
        raise HTTPException(422, "unknown endpoint")

    return {"endpoint": endpoint, "collection": coll, "explain": plan}

