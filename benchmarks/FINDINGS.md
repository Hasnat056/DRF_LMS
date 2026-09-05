# Endpoint performance audit — findings

65 GET endpoints against a stress dataset (5,000 students, 200 faculty, 600
allocations, 75,000 enrollments, 250,000 marks). Numbers in `results.md`,
regenerated on every run; this file is the analysis and is not overwritten.

---

## 0. A correction to the first run

The first version of this audit was wrong and its numbers should not be used.

The root `conftest.py` sets `CELERY_TASK_ALWAYS_EAGER = True` as an autouse
fixture. Five admin list views answer a cache miss by publishing a cache-rebuild
job and returning. Under eager execution that job ran **inline, inside the
request being timed**. So every "cold" figure for those endpoints was the
request plus a full cache rebuild — work production does on a worker.

The scale of the error:

| Endpoint | First run | Actual |
|---|---:|---:|
| `admin/student-list` | 342 q / 6,525 ms | 66 q / 112 ms |
| `admin/faculty-list` | 149 q / 347 ms | 45 q / 104 ms |

The tell was visible in the first table and missed: three different query-param
branches of `admin/faculty-list` all reported an identical 149 queries cold.
Three different code paths cannot agree to the query unless the dominant cost is
the same param-independent thing in all of them.

The audit now runs against a real worker (`celery-worker-test`) on its own
broker, and each endpoint waits for a quiet worker before it is timed.

## 1. What the "fill" column means

With a real worker, a cache miss returns immediately and the cache is filled a
moment later. The harness waits for the key to appear and records that wait.

That number is the one that matters operationally: **after any write, this is
how long every user keeps hitting the database.** It did not exist in the first
run, because eager execution hid it inside the request.

---

## 2. Fixed during the audit

| Endpoint | Before | After |
|---|---:|---:|
| `student/enrollments` | 5,134 q / 6,886 ms | 126 q / 275 ms |
| `admin/student-list?program` | never hit its cache | 67 → 2 q |
| `admin/student-list?student_class` | not cached | 67 → 2 q |

`student/enrollments` was the worst endpoint in the audit. Its serializer
declared `assessmentchecked_set` as a nested `many=True` field, which is the
reverse accessor from **Assessment** — so it built every enrolled student's
marks for every assessment, then `to_representation` discarded all of it and
kept only the requesting student's row. Cost scaled with class size rather than
with the requester. A filtered `Prefetch` on the list queryset fixed it.

The `?program` branch read `admin:students:program:{query_params.get("program_id")}`
while the filterset field is `program`, so the lookup was always `...:program:None`
and never matched what the task wrote. Separately, the cached branch keyed on
`class_`, which is not a filterset field at all, while `student_class`, which is,
had no cache. Both corrected.

---

## 3. Remaining problems, worst first

### 3.1 `admin/dashboard` — 8,455 ms, 17 queries

One query accounts for 7,179 ms of it:

```sql
SELECT department.department_id,
       COUNT(DISTINCT Student.student_id_id)  AS student_count,
       COUNT(DISTINCT Faculty.employee_id_id) AS faculty_count,
       COUNT(DISTINCT program.program_id)     AS program_count
FROM department ...
```

Three independent one-to-many joins hung off one table. MySQL builds their
cartesian product — students x faculty x programs per department — and
`COUNT(DISTINCT ...)` then de-duplicates it. At 1,000 students and 40 faculty
per department that is a 40,000-row intermediate **per department**, for three
numbers that are individually trivial.

Two more queries in the same view cost 307 ms and 293 ms (enrollment counts
grouped by status and by year).

**Fix:** three separate aggregate queries, or `Subquery`/`OuterRef` per count.
This is the single biggest win left in the codebase.

### 3.2 `faculty/dashboard` — 1,024 queries

1,000 of them are one shape:

```sql
SELECT result.* FROM result WHERE result.enrollment_id = ? LIMIT 1
```

One per enrollment across the teacher's classes. **Fix:** `select_related('result')`
on the enrollment queryset.

### 3.3 The detail endpoints — ~390 queries each

Four endpoints share one shape: they serialise a whole class one student at a
time.

| Endpoint | Queries | Repeated |
|---|---:|---|
| `faculty/allocation-detail` | 389 | 126 x `person`, 125 x `Student`, 125 x `result` |
| `admin/allocation-detail` | 386 | 125 x `Student`, 125 x `person`, 125 x `result` |
| `faculty/lecture-detail` | 384 | 126 x `person`, 125 x `enrollment`, 125 x `Student` |

**Fix:** `select_related('student__student_id', 'result')` on the enrollment
queryset behind each.

### 3.4 `student/enrollment-detail` — 269 queries

251 of them are `SELECT ... FROM enrollment WHERE enrollment_id = ? LIMIT 1`.

This is the same bug that was just fixed on the list view, still present on the
detail view — the filtered `Prefetch` was moved rather than added to both. The
per-row cost comes from `AssessmentCheckedHyperlinkedIdentityField.get_url`:

```python
'enrollment_id': obj.enrollment.enrollment_id,   # FK fetch, one query per row
'assessment_id': obj.assessment.assessment_id,
```

`obj.enrollment_id` and `obj.assessment_id` are already on the row and cost
nothing. The guard two lines above already uses `obj.assessment_id` correctly.
Changing those two attribute accesses removes the queries with no other change.

### 3.5 `admin/enrollments` — 585 ms

Only 37 queries, but 435 ms of it is the single unpaginated-scan query over
`enrollment`. Worth an index review rather than a prefetch.

### 3.6 `admin/allocations?search` — 684 ms on 6 queries

324 ms and 321 ms, both from the same correlated subquery:

```sql
... WHERE EXISTS (SELECT 1 FROM courseAllocation U0
                  JOIN Faculty U1 ... JOIN person U2 ... )
```

DRF's `SearchFilter` generates an `EXISTS` subquery because `search_fields`
crosses a reverse relation (`enrollment__student__student_id__first_name`).
Dropping that one field from `search_fields`, or replacing the search with an
explicit filter, removes both costs.

### 3.7 `admin/student-list` — 66 queries for 10 rows

Per page of 10: 11 x `auth_user`, 10 x `person`, 10 x `address`,
10 x `qualification`. The view's queryset is bare:

```python
queryset = Student.objects.all()             # views.py
```

while `cache_student_data_task` feeds the *same* serializer a fully prefetched
queryset (`tasks.py`). The prefetching already exists in this codebase; it is
just not on the view. So the uncacheable `?search` path — the one users hit most
— is the only path that does not get it.

---

## 4. The cache design problem

**Every cache task rebuilds everything on any write.**

`cache_enrollment_data_task` is the worst case. It loads all 75,000 enrollments
and serialises them twice in pure Python — once grouped by student, once by
teacher — writing about 5,200 keys. It is fired from `perform_create`,
`perform_update` and `perform_destroy`, so **one student enrolling in one course
rebuilds the cache for every student and every teacher in the system.**

Measured consequence: both enrollment filter endpoints report `fill > 60s`. The
cache never arrives before the next request, so it costs three CPU cores for
minutes and returns nothing. During the audit this task alone saturated the
worker and starved every other job behind it.

One enrollment changing affects exactly two keys: that student's, and that
enrollment's teacher's. The task takes `user_id`, but that is only used to build
URLs in the serializer — it scopes nothing. Passing the changed enrollment
instead would take the rebuild from 5,200 keys to 2.

On delete, if it was the student's last enrollment, the key should be removed
rather than rebuilt empty.

The same pattern is in the student, faculty, course and semester tasks. Enrollment
is simply where it hurts most, because it is the largest table. Related figures:
`admin/student-list?status` takes **9.8 seconds** to fill, so every student write
opens a ten-second window in which admins hit the database.

A second, smaller point: each task calls `cache.delete(key)` before rewriting it,
so during a rebuild the cache is not stale — it is *absent*, and everyone falls
through. Writing over the key would shorten that window to nothing.

---

## 5. Where caching is working

Faculty and programs rebuild in 21–150 ms and drop to 2 queries warm. Those
caches are earning their keep. So are `admin/courses`, `admin/programs?dept`,
`admin/allocations?semester` and `?faculty`, `student/dashboard`,
`faculty/profile` and `faculty/allocations`.

`admin/allocations-bulk` measures 7 queries cold and 4 warm at 40 classes and is
flat as class count grows — the split between a cached skeleton and live
allocations is doing exactly what it was designed to do.

---

## 6. Suggested order of work

1. `admin/dashboard` — split the three-way `COUNT(DISTINCT)` join. Biggest win.
2. `student/enrollment-detail` — `obj.enrollment_id` instead of
   `obj.enrollment.enrollment_id`. Two characters, ~250 queries.
3. `faculty/dashboard` — `select_related('result')`.
4. The three allocation/lecture detail endpoints — one shared prefetch fix.
5. Scope `cache_enrollment_data_task` to the affected keys.
6. Move the prefetching from `cache_student_data_task` onto the student list view.
7. `admin/allocations?search` — narrow `search_fields`.

---

## 7. Method

Median of 5 runs per endpoint. Each endpoint: purge the queue and wait for the
worker to go idle, one discarded warm-up request, then five rounds of
`cache.clear()` → cold request → wait for the key → warm request.

Query counts come from `CaptureQueriesContext`, which works without `DEBUG=True`;
turning `DEBUG` on would itself change what is being measured.

Read the query counts as exact — they were identical across runs. Read the
millisecond figures as magnitudes: they moved about 10% run to run, and rows
measured while the worker was rebuilding a cache are pessimistic by roughly that
much again, since the worker and the request share CPU. That contention is real
in production too.

Causes in section 3 are not inferred from the counts. They come from
`test_query_profile.py`, which collapses every captured statement to its shape
and counts the repeats.

No application code was changed by the audit itself. The fixes in section 2 were
made by the author in response to it.
