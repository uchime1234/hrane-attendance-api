from datetime import datetime

# Step 1: make two moments
arrived = datetime(2026, 7, 6, 9, 0, 0)
left    = datetime(2026, 7, 6, 11, 0, 0)

# Step 2: subtract them
diff = left - arrived
print("difference object:", diff)
print("difference type:  ", type(diff))

# Step 3: ask for seconds
secs = diff.total_seconds()
print("seconds:          ", secs)

# Step 4: convert to hours
hours = secs / 3600
print("hours:            ", hours)

# Step 5: try Python's rounding
print("round(2.675, 2):  ", round(2.675, 2))
print("round(2.685, 2):  ", round(2.685, 2))

# Step 6: try Decimal rounding
from decimal import Decimal, ROUND_HALF_UP
d = Decimal("2.675").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
print("Decimal 2.675 half-up:", d)

from datetime import datetime

def compute_late_minutes(punch_in, shift_start):
    punch_in = punch_in.replace(microsecond=0)
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    seconds = (punch_in - start).total_seconds()
    if seconds > 600:
        return int(seconds // 60)
    return 0

for hh, mm, ss in [(9, 40, 0), (9, 40, 1), (10, 15, 59)]:
    t = datetime(2026, 7, 6, hh, mm, ss)
    print(f"{hh:02d}:{mm:02d}:{ss:02d} -> {compute_late_minutes(t, '09:30')}")



import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
db = MongoClient(os.getenv("MONGO_URI"), tz_aware=True)[os.getenv("MONGO_DB")]

# Set EMP0002's late_minutes to 35 so it ties with EMP0003
result = db.attendance_logs.update_one(
    {"emp_code": "EMP0002", "date": "2026-07-14"},
    {"$set": {"late_minutes": 35}},
)
print("matched:", result.matched_count, "modified:", result.modified_count)