# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |

| 1 | health() endpoint | It always returns a 200 status code, even when the mongo db service is down, but the contracts expects a 503 | when i intentionally stopped mongo db service, the code still returned a 200 status code  | i wrote a try error statment, if the mongo db is running, it returns a 200 if its down it raises a 503 error |

| 2 | compute_work_hours | Uses round(), which does banker's rounding (2.675 → 2.67, should be 2.68 per R4). Also doesn't truncate instants to whole seconds first (violates R1) | Punch in 09:00:00, punch out 11:40:30 → 2.675 hours. Expected 2.68 per R4, got 2.67.| Truncate both datetimes with .replace(microsecond=0), divide seconds by 3600 using Decimal, round to 2 places with .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), return as float. |

| 3 | compute_late_minutes | The late check compares the floored minutes to 10 (minutes > 10), so a punch-in at 09:40:01 is floored to 10 and treated as not late. Also doesn't truncate to whole seconds (R1). | Shift 09:30, punch_in 09:40:01. R2 says return 10. Starter returns 0. | Truncate punch_in to whole seconds; compute elapsed seconds; if seconds > 600 return seconds // 60, else 0. |

| 4 | compute_overtime | Missing R3's 30-minute minimum, so 20 minutes of overtime returns 20 instead of 0. Also builds the shift-end datetime on the record's date, which is wrong for overnight shifts (shift_end <= shift_start) where the shift ends the next calendar day. Doesn't truncate punch_out to whole seconds (R1). | Shift 09:30–18:30, punch_out 18:50 → expected 0 per R3, starter returns 20. Overnight 22:00–06:00, punch_out next-day 06:40 → expected 40, starter computes a ~24-hour value clamped to 0. | Added shift_start parameter; truncate punch_out to seconds; if shift_end <= shift_start add one day to end; return minutes only when minutes >= 30, else 0. |

| 5 |	punch_in	| No check for unknown employee; emp["shift_start"] crashes with 500 |	POST with an emp_code that isn't in employees → 500	| wrote an if statement to find_one; if None, raise 404 |

| 6	| punch_in | datetime.fromtimestamp()/datetime.now() use server local timezone, not UTC | On a server in WAT, a punch-in sent as 09:25 IST is stored at the wrong instant |	Pass tz=timezone.utc explicitly |

| 7	| punch_in | date computed from UTC instant, not IST; which violates R1 for punches near midnight |	Punch-in at 23:00 UTC → stored date is previous day, should be next day in IST | ts.astimezone(IST).date().isoformat() |

| 8	| punch_in	| No truncation to whole seconds (R1) | Send a punched_at ending in ...900 ms → stored value has extra 900 ms |ts.replace(microsecond=0) |

| 9 |	punch_in |	Check-then-insert race: two simultaneous punch-ins both pass find_one and both insert |	Fire two identical POSTs in parallel → two records for the same (emp_code, date) |	Unique index on (emp_code, date) + try/except DuplicateKeyError → 409 |

| 10 |	punch_in |	Response includes "id" field; contract has no id; also punch_in returned as a datetime string, not epoch ms	Compare response JSON against AttendanceRecord schema |	record_to_api(doc) drops _id and converts datetimes to epoch ms |

| 11 | create_employee | It checks if the emp_code already exists, then inserts. If two people create the same employee at the same time, both checks pass and you get two employees with the same code | Send two POSTs with the same emp_code at the same time → two employees saved instead of one | I made emp_code a unique index in the DB and wrapped the insert in a try/except. If MongoDB rejects it as a duplicate, I raise a 409 |

| 12 | create_employee | It saves created_at using the server's local time instead of UTC, and it returns it as a datetime string instead of epoch milliseconds like the contract says | Look at the response — created_at shows as "2026-10-12T15:07:27" instead of a number like 1783... | I changed it to datetime.now(timezone.utc) and wrote an employee_to_api helper that converts it to int(dt.timestamp() * 1000) before returning |

| 13 | create_employee | It doesn't check that shift_start and shift_end are different, so you can create an employee with a zero-length shift | POST with shift_start "09:30" and shift_end "09:30" → it saves it (should give 422) | Added a check: if shift_start == shift_end, raise HTTPException 422 |

| 14 | list_employees | The pagination math uses "page * page_size", but the rule says pages start at 1. So page=1 skips the first page and returns the second page instead | Have 25 employees, request page=1&page_size=20 → you get items 21-25 (only 5), should get items 1-20 | Changed to (page - 1) * page_size |

| 15 | list_employees | total uses count_documents({}) which counts every employee in the DB, but R10 says total must reflect the filter that was applied | Filter by department=Engineering with 3 matches out of 100 total → response shows total: 100, should be 3 | Changed to count_documents(q) using the same filter as the query |

| 16 | list_attendance | Loads every matching document into Python with list(find(q)), sorts and pages in Python. Spec says this must stay fast at 100k records and must not load the collection into memory | With 100k attendance records, the endpoint is slow and memory-heavy because every doc comes into the process first | Pushed the sort, skip and limit into MongoDB: .sort([...]).skip(...).limit(...) and used count_documents(q) for the total |

| 17 | list_attendance | Sort is only by date descending. Contract says date descending, then emp_code ascending. So two records on the same date come back in arbitrary order | Two records on the same date (e.g. EMP0003 and EMP0004 on 2026-07-08) come out in the wrong order | .sort([("date", -1), ("emp_code", 1)]) |

| 18 | list_attendance | Response includes an "id" field made from _id, and returns punch_in/punch_out/history times as datetime strings instead of epoch milliseconds. Contract has no id field and requires integer ms | Compare response JSON against AttendanceRecord schema: shows "id": "6acbfb..." and "punch_in": "2026-07-14T04:02:00+00:00" | Pass every doc through record_to_api, which drops _id and converts all datetimes to epoch ms |

| 19 | list_attendance | Doesn't check that date_from <= date_to. Contract requires 422 for an inverted range | GET /attendance?date_from=2026-07-10&date_to=2026-07-01 returns 200 with an empty list, should be 422 | if date_from and date_to and date_from > date_to: raise HTTPException(422, ...) |

Also note anything you looked at and decided was **not** a defect, and why.
