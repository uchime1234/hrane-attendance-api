# DECISIONS.md

**1. Indexes.** I created four indexes. `employees.emp_code` is unique — it enforces the "one employee per code" rule and makes lookups fast. `attendance_logs.(emp_code, date)` is unique — it enforces the one-record-per-employee-per-day rule, which is what makes concurrent punch-ins safe (MongoDB rejects the second insert with `DuplicateKeyError`). `attendance_logs.(date, emp_code)` serves the list endpoint's sort by date desc, emp_code asc. `attendance_logs.(status, date)` backs the analytics filters. I considered an index on `status` alone but rejected it: too many documents share the same status, so it wouldn't narrow the result enough.

**2. Punch-in race.** Two identical punch-ins arrive. Both go to `insert_one` on `attendance_logs`. MongoDB's unique index on `(emp_code, date)` accepts exactly one; the second raises `DuplicateKeyError`. The endpoint catches that and returns 409. I never check-then-insert — that would allow both to pass the check and both to insert.

**3. Ties.** `$setWindowFields` with `$rank` gives tied employees the same rank (1, 2, 2, 4). `limit` is applied with `$match: {rank: {$lte: limit}}` **after** ranking, so a tie at the cutoff returns every tied employee. The response can have more rows than `limit`.

**4. Headcount.** The department summary pipeline starts on the `employees` collection, not `attendance_logs`, and `$lookup`s the logs. That way employees with zero logs are still in the pipeline and counted. Employees who join after the month's end are excluded by a `joined_on` filter.

**5. At 100× data.** I would shard `attendance_logs` on `emp_code` (the natural partition for both write-heavy punch-ins and most queries), and precompute monthly summaries into a separate `monthly_summaries` collection that gets updated on write. The current analytics pipelines are efficient but recompute from scratch every call; at 100× the reads would dominate.