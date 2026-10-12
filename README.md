# Employee Attendance & Analytics API

FastAPI + MongoDB service implementing the HROne engineering assignment contract.

## Requirements
- Python 3.11+
- MongoDB 6.0+

## Setup

1. Create a `.env` at the repo root with:
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

text

2. `pip install -r requirements.txt`
3. `python sample_seed.py` (optional — loads the sample data)
4. `uvicorn app.main:app --port 8000`

API docs: http://localhost:8000/docs

## Status
Work in progress. Missing:
- `POST /attendance/punch-out`
- `PATCH /attendance/{emp_code}/{date}`
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}`
- Indexes not yet created at startup

## Files
- `app/main.py` — all application code
- `REVIEW.md` — Part A: starter defects and fixes
- `DECISIONS.md` — Part C: design decisions