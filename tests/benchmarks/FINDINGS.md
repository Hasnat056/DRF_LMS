# Endpoint performance audit — findings

65 GET endpoints against a stress dataset (5,000 students, 200 faculty, 600
allocations, 75,000 enrollments, 250,000 marks, 37,500 attendance rows).

Measured, fixed, measured again. The "after" figures below are the **mean of
three consecutive runs** taken back to back on an idle machine, with the Celery
worker verified idle and Redis flushed before each. `results.md` holds the raw
output of the most recent run and is regenerated every time; this file is the
analysis and is not overwritten.

| | before | after |
|---|---:|---:|
| Cold queries, all 65 endpoints | 4,363 | **471** |
| Cold time, all 65 endpoints | 17,266 ms | 1,174 ms |
| Warm time, all 65 endpoints | 6,309 ms | 992 ms |
| Worker time to refill every key after one invalidation | — | 19,657 ms |

**Read the query row as exact and the time rows as approximate.** Across the
three runs no endpoint changed its query count by even one — not once, in 195
measurements. The times moved 6.7% (cold), 9.1% (warm) and 10.5% (fill) at the
whole-suite level, and the *median individual endpoint* moved ±17%, with the
worst at ±56%. The noisiest endpoints are the fastest ones, where a few
milliseconds of scheduling jitter is a large fraction of the total. Do not read
a single endpoint's millisecond change as a result unless it is a large one.
Section 4 covers the method and a machine-speed problem that invalidates
cross-day time comparisons.

---

## 1. The caching layer no longer earns its keep

This is the audit's main finding, and it only became visible once the N+1s were
gone.

Cold and warm are now nearly the same. Across all 65 endpoints the cache saves
**182 ms, 15.5%**. Restricting to the 27 endpoints that actually have a cache:

| | cold | warm | saved |
|---|---:|---:|---:|
| 27 cached endpoints | 562 ms | 397 ms | **165 ms (29%)** |
| 38 uncached endpoints | 612 ms | 594 ms | 17 ms — noise, they have no cache |

165 ms saved, in exchange for **19,657 ms of worker time** to refill the keys
after a single invalidation. That is a ratio of roughly **119 to 1**. A full
sweep of all 27 cached endpoints saves 165 ms, so the cache has to serve about
119 such sweeps — call it 3,200 cached requests — between one write and the
next merely to break even on CPU. Every write to a cached entity resets that
counter.

The reason is that the cache was competing against a database path that used to
cost 4,363 queries and now costs 471. When a list endpoint answers from MySQL
in 15 ms, Redis cannot save much.

### 1.1 Five endpoints are actively slower warm than cold

| endpoint | cold | warm | "saving" | fill |
|---|---:|---:|---:|---:|
| `admin/student-list?dept` | 29.7 ms | 56.8 ms | **−27.0 ms** | 1,490 ms |
| `admin/student-list?dept+status` | 33.1 ms | 58.2 ms | **−25.1 ms** | 1,769 ms |
| `admin/student-list?program` | 22.9 ms | 44.7 ms | **−21.8 ms** | 3,688 ms |
| `admin/student-list?student_class` | 19.2 ms | 36.5 ms | **−17.3 ms** | 4,588 ms |
| `admin/student-list?status` | 50.5 ms | 57.5 ms | **−7.0 ms** | 6,215 ms |

Not a measurement error. These are the endpoints whose cache-miss branch checks
the *unfiltered* list key first and fires a full student rebuild regardless of
which filter was asked for. The rebuild takes 1.5–6.2 seconds, and the warm
request is timed while the worker is still running it, competing for CPU. The
cache makes these endpoints slower, and it does so precisely when they are
being used.

Those six student keys account for **18,836 of the 19,657 ms** of total fill.
Everything else put together is 821 ms.

### 1.2 The scoped tasks, by contrast, are almost free

The thirteen keys whose rebuild was scoped in this audit refill in 0–287 ms,
and seven of them returned an *identical* figure in all three runs —
`admin/faculty-list?dept` was 62 ms three times, `?dept+desig` 82 ms three
times. That is deterministic behaviour, not a fast average.

| key | fill before | fill after |
|---|---:|---:|
| `admin/allocations?faculty` | 3,541 ms | **14 ms** |
| `admin/allocations?semester` | 2,151 ms | **14 ms** |
| `admin/semesters?class` | 1,700 ms | **61 ms** |
| `admin/semesters` | 870 ms | **43 ms** |
| `admin/enrollments?student` | never arrived | **21 ms** |
| `admin/enrollments?faculty` | never arrived | **135 ms** |

### 1.3 What to do about it

Not decided — this is a recommendation, not a change.

The evidence says the honest options are:

1. **Scope the student-list rebuild the way the others were scoped.** The
   task already accepts `student_groups`; the *list view's cache-miss branch*
   is what still asks for everything. This is the smallest change and would
   remove ~18.8 s of the 19.7 s.
2. **Delete the cache on the filtered student and faculty list branches.**
   They now cost 7–8 queries against the database. The cache saves single-digit
   milliseconds on most of them and is negative on five.
3. **Keep it and accept the cost**, on the grounds that production has more
   data than the stress set and the ratio may look different at that scale.

Option 1 is the clear first move: it is small, it is the same fix already
applied four times, and it is the only one that does not need a judgement call
about production load.

---

## 2. What was fixed

31 endpoints dropped query counts; two gained one each. Every fix is the same
shape — a view whose queryset fetched less than its serializer reads, so the
serializer went back to the database once per row.

| endpoint | queries |
|---|---|
| `faculty/dashboard` | 1,024 → **5** |
| `faculty/allocation-detail` | 389 → **14** |
| `admin/allocation-detail` | 386 → **11** |
| `faculty/assessment-detail` | 386 → **11** |
| `faculty/lecture-detail` | 384 → **9** |
| `student/enrollment-detail` | 269 → **8** |
| `student/enrollments` | 126 → **6** |
| `admin/semesters` | 84 → **5** |
| `student/attendance` | 74 → **5** |
| `admin/student-list` (8 variants) | 66 → **7** |
| `admin/faculty-list` (6 variants) | 45 → **6** |
| `admin/enrollments` (3 variants) | 37 → **7** |
| `admin/classes` | 34 → **6** |
| `admin/semesters?class` | 29 → **6** |
| `student/attendance-detail` | 13 → **7** |
| `admin/class-detail` | 6 → **5** |

`admin/dashboard` kept its 17 queries but went from 8,455 ms to 59 ms; the
problem there was one query's shape, not the count.

### 2.1 `admin/dashboard` — one query was 7,179 ms

Three independent one-to-many joins hung off `department`, so MySQL built their
cartesian product — students × faculty × programs per department, about 40,000
rows each — and `COUNT(DISTINCT ...)` de-duplicated it afterwards, to produce
three individually trivial numbers.

Now one correlated subquery per relation, each counting on its own index.

Two details worth recording. A fourth annotation, `enrollment_count`, was
computed over 75,000 rows and then discarded, because it was never named in the
`values()` output — deleted. And the old query's `GROUP BY` was sorting the
result rows as a side effect; the subquery version has no `GROUP BY`, so the
ordering had to be made explicit or the payload would have quietly changed
order.

### 2.2 `faculty/dashboard` — 1,024 queries to 5

1,000 of them were `SELECT result WHERE enrollment_id = ?`, one per enrollment
across the teacher's classes, from a Python loop averaging marks.

Replaced with one aggregate per completed allocation. `enrollment → result` is
one-to-one so the join cannot fan out, and enrollments with no result contribute
NULL, which `SUM` skips — which is what the old `hasattr` guard did. The three
separate `.count()` calls became one aggregate, and the `prefetch_related`
became `select_related`, since every use of it was a filtered count that
re-queries and ignores a prefetch cache anyway.

### 2.3 The detail endpoints — ~390 queries each to 9–14

Four endpoints serialised a whole class one student at a time: 125 × `Student`,
125 × `person`, 125 × `result` on the allocation details, and the same walk
through `obj.enrollment.student.student_id` on the assessment and lecture
details. One prefetch each.

`student/enrollment-detail` was different: 251 of its 269 queries were
`SELECT enrollment WHERE enrollment_id = ?`, from
`AssessmentCheckedHyperlinkedIdentityField.get_url` reading
`obj.enrollment.enrollment_id` — fetching the parent row to read an id already
sitting on the child. The guard two lines above it already used
`obj.assessment_id` correctly.

### 2.4 Model ordering pulled in joins nobody asked for

`Enrollment.Meta.ordering` was `['enrollment_id', 'student', 'allocation']`.
`student` and `allocation` are foreign keys, and ordering by a foreign key makes
Django substitute *that model's* own `Meta.ordering`, recursively. The statement
that came out had **7 joins and a 14-column ORDER BY** to return 10 rows, and
MySQL had to build and sort the joined set before applying the `LIMIT`. That one
query was 477 ms.

All of it was dead weight: the first sort key is the primary key, so nothing
after it could break a tie.

The same shape was in four more models. `Faculty`, `Student` and `Admin` each
led with a `OneToOneField` to `Person` that is also their own primary key —
still a foreign key as far as ordering expansion is concerned, so each pulled in
`person`'s name columns for a tiebreak that could never fire. `CourseAllocation`
led with its own `AutoField` PK and two dead FK columns behind it. All five now
sort on the raw column with zero joins, and all five were verified to return
identical row sequences against real data.

Migration `0020` carries the change. It is `AlterModelOptions` only, so it emits
no SQL at all — `sqlmigrate` prints `(no-op)` for every operation.

### 2.5 `cache_enrollment_data_task` — the rebuild that never finished

The worst thing the audit found, and the least visible.

The task loaded all 75,000 enrollments, serialised them twice in Python, and
wrote about 5,200 keys. It fired from `perform_create`, `perform_update` and
`perform_destroy`, so **one student enrolling in one course rebuilt the cache
for every student and every teacher in the system.** Both enrollment filter
endpoints reported `fill > 60s`: the cache never arrived, cost three CPU cores
for minutes, and returned nothing.

It now takes optional `student_ids` / `faculty_ids`. Each write path passes the
keys it actually affects — `perform_update` passes the old owners too, since an
enrollment can move to another allocation and leave the previous teacher's key
wrong.

Two smaller things in the same task: its queryset was bare behind a serializer
that reads `obj.student.student_id` and `obj.result` per row, running 75,000
times; and each key was deleted before being rewritten, so during a rebuild the
cache was not stale but *absent*, and every reader fell through to the database.
Writing over the key closes that window.

On delete, an empty list is now cached rather than the key being removed. Empty
is the true answer, and the view treats only `None` as a miss — a deleted key
would miss forever and re-fire a rebuild on every read.

### 2.6 The other cache tasks rebuilt everything too

The same pattern was in the student, faculty, semester and course-allocation
tasks. Each now takes an optional scoping parameter naming the groups a write
actually touched — a set of group keys, not just an id, because unlike
enrollment's per-student keys these caches are partitioned by department,
program, class, designation and status. A write passes both the old and the new
group, since a row can move between groups in one write.

`SemesterListAPIView` now shares `_semester_cache_queryset()` with its cache
task, so the view and the worker cannot drift. That drift was the original bug
on the student and faculty lists: the task built a fully prefetched queryset
while the view's own was bare, so the cached branches were fast and `?search`
and `?ordering` — the branches admins actually use — paid 45–66 queries.

### 2.7 Two serializer faults worth naming

**A prefetch built and then discarded.** `student/enrollments` prefetched every
marked-assessment row for the student in one query, and then
`StudentAssessmentSerializer.to_representation` called
`assessmentchecked_set.filter(...)`, which abandons the prefetch cache and
issues a fresh query — 40 of them on a page of ten, on top of the prefetch that
still ran. `.all()` reuses prefetched rows; adding any filter does not.

The fallback query is kept deliberately. `StudentEnrollmentRetrieveView` shares
the serializer, and a bare `.all()` on a caller with no prefetch would return
every student's row for that assessment rather than the requesting student's.
The original filtered code was correct — it was only slow.

**A field that built its own queryset.** `SchemeOfStudiesField.to_representation`
ran `Semester.objects.filter(associated_class=obj.class_id)` once per class, so
it could never see a prefetch the view had done — 30 of `admin/classes`'s 34
queries. It now reads `obj.semester_set.all()`.

**A column read off an unfetched parent.** `self.instance.enrollment.status` in
a serializer `__init__` fetched a whole `Enrollment` row to read one column, for
rows the view had already loaded. Django has no identity map, so it could not
reuse them. Fixed by carrying `enrollment` on the prefetch queryset — the join
was already there for the filter, only the columns were missing.

---

## 3. What is still open

### 3.1 The student-list cache — see 1.3

The largest remaining item, and the only one with hard numbers behind it.

### 3.2 Two endpoints gained a query

| endpoint | queries |
|---|---|
| `faculty/lectures` | 14 → **15** |
| `faculty/assessments` | 9 → **10** |

The prefetches added in 2.3 went onto the list views as well as the detail
views, which costs one query to fetch the nested rows for the whole page. On a
detail endpoint that trades ~375 queries for one and is obviously right. On
these two lists there were few nested rows to begin with, so it is one query
bought for nothing. Worth reverting on the list views, or worth keeping as
insurance against a class with more data — but it should be a decision.

### 3.3 `StudentEnrollmentRetrieveView`'s fallback

The retrieve view now carries the same `Prefetch` as the list view, so the
fallback branch in `StudentAssessmentSerializer.to_representation` is no longer
exercised by either view. It is kept as a safety net for any future caller that
forgets the prefetch. It could be deleted if that guarantee is made explicit.

### 3.4 `admin/student-detail` — 30 queries

The highest remaining count. Not investigated.

---

## 4. Method

Median of five runs per endpoint, and the reported figures are the mean of three
such whole-suite runs. Each endpoint: purge the queue and wait for the worker to
go idle, one discarded warm-up request, then five rounds of `cache.clear()` →
cold request → wait for the key → warm request.

- **Cold** — the first request after `cache.clear()`.
- **Fill** — how long after that request the Celery worker finished writing the
  key, measured against a real worker on its own broker, not eager execution.
  This is how long every user keeps paying the cold path after any write.
- **Warm** — the request after the key was confirmed present.

Query counts come from `CaptureQueriesContext`, which works without
`DEBUG=True`; turning `DEBUG` on would itself change what is being measured.
Causes are not inferred from counts — they come from `test_query_profile.py`,
which collapses every statement to its shape and counts the repeats.

### Timings are only comparable within a run

This is stronger than the usual "timings are noisy", and it is measured rather
than assumed.

Earlier in the audit the same 57 endpoints — unchanged query counts, untouched
code — ran **2.6× slower** between two runs hours apart. Cold time on them went
1,094 → 2,818 ms (2.58×) and warm 891 → 2,349 ms (2.64×), two independent
measures agreeing to within 2%. The cause was host memory pressure: 1.5 GB
swapped out with 406 MB free, and `vmstat` showing 25 MB/s of swap-in. The full
test suite went from 4 m 14 s to 9 m 02 s over the same period and back down
again afterwards.

Consequently the before/after time comparison in the headline table mixes two
effects and should not be quoted as a speed-up factor. The query comparison is
clean. Re-measuring the pre-fix baseline on today's machine would fix this and
was not done.

Worker state was verified before each of the three final runs: no pytest
process running, worker responding to `inspect ping`, `inspect active` and
`inspect reserved` both empty, broker queue length 0, and Redis databases 0, 2
and 3 flushed. Two earlier measurement failures make this worth doing every
time:

- **A stale worker reads as a regression.** Celery does not reload changed
  modules. After editing a cache task, the worker kept executing the previous
  version and `admin/semesters` reported a fill of 1,540 ms instead of 86 ms.
  Restart the worker and confirm its start time is later than the edit.
- **A stopped run is not a stopped run.** The suite runs as
  `docker compose exec backend pytest`. Killing the client leaves pytest running
  inside the container; starting another produced 22 deadlock errors across
  modules that had not been touched.

### A correction to the first run

The first version of this audit was wrong and its numbers should not be used.

The root `conftest.py` sets `CELERY_TASK_ALWAYS_EAGER = True` as an autouse
fixture. Five admin list views answer a cache miss by publishing a cache-rebuild
job and returning. Under eager execution that job ran **inline, inside the
request being timed** — so every "cold" figure for those endpoints was the
request plus a full cache rebuild, work production does on a worker.
`admin/student-list` read 342 q / 6,525 ms when it was actually 66 q / 112 ms.

The tell was in the first table and was missed: three different query-param
branches of `admin/faculty-list` all reported an identical 149 queries cold.
Three different code paths cannot agree to the query unless the dominant cost is
the same param-independent thing in all of them.

`tests/benchmarks/conftest.py` now shadows that fixture for this directory only,
and the audit runs against a real worker.
