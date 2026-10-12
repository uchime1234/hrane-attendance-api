# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |

| 1 | health() endpoint | It always returns a 200 status code, even when the mongo db service is down, but the contracts expects a 503 | when i intentionally stopped mongo db service, the code still returned a 200 status code  | i wrote a try error statment, if the mongo db is running, it returns a 200 if its down it raises a 503 error |

| 2 | compute_work_hours | Uses round(), which does banker's rounding (2.675 → 2.67, should be 2.68 per R4). Also doesn't truncate instants to whole seconds first (violates R1) | Punch in 09:00:00, punch out 11:40:30 → 2.675 hours. Expected 2.68 per R4, got 2.67.| Truncate both datetimes with .replace(microsecond=0), divide seconds by 3600 using Decimal, round to 2 places with .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), return as float. |

| 3 | compute_late_minutes | The late check compares the floored minutes to 10 (minutes > 10), so a punch-in at 09:40:01 is floored to 10 and treated as not late. Also doesn't truncate to whole seconds (R1). | Shift 09:30, punch_in 09:40:01. R2 says return 10. Starter returns 0. | Truncate punch_in to whole seconds; compute elapsed seconds; if seconds > 600 return seconds // 60, else 0. |

| 4 | compute_overtime | Missing R3's 30-minute minimum, so 20 minutes of overtime returns 20 instead of 0. Also builds the shift-end datetime on the record's date, which is wrong for overnight shifts (shift_end <= shift_start) where the shift ends the next calendar day. Doesn't truncate punch_out to whole seconds (R1). | Shift 09:30–18:30, punch_out 18:50 → expected 0 per R3, starter returns 20. Overnight 22:00–06:00, punch_out next-day 06:40 → expected 40, starter computes a ~24-hour value clamped to 0. | Added shift_start parameter; truncate punch_out to seconds; if shift_end <= shift_start add one day to end; return minutes only when minutes >= 30, else 0. |


Also note anything you looked at and decided was **not** a defect, and why.
