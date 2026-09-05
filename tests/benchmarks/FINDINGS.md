# Endpoint performance audit — findings

65 GET endpoints against a stress dataset (5,000 students, 200 faculty, 600
allocations, 75,000 enrollments, 250,000 marks).

Measured, fixed, then measured again. Both runs used the same dataset, the same
harness and a real Celery worker. `results.md` holds the current numbers and is
regenerated on every run; this file is the analysis and is not overwritten.

| | before | after |
|---|---:|---:|
| Cold queries, all 65 endpoints | 4,363 | **698** |
| Cold time, all 65 endpoints | 17,266 ms | **1,370 ms** |
| Benchmark suite wall clock | 29 m 32 s | **4 m 48 s** |

**Read the query row as exact and the millisecond row as approximate.** Query
counts repeat identically run to run. Milliseconds do not: this machine got
steadily faster over the day as MySQL's buffer pool warmed, and between the
last two runs — which differ by two fixes touching seven endpoints — the
*warm* total for all 65 fell 2,364 ms to 1,082 ms. Warm requests read Redis
and run the same two queries in every run; nothing changed that could make
them faster. Roughly 1,240 ms of the last run's improvement sits on endpoints
whose query count did not change at all, and that share belongs to the
machine, not to this work. Section 4 has the detail.

The full suite passes (779 tests). Both rewritten aggregates were run against
the queries they replaced and return identical rows.

---

## 1. What was fixed

| Endpoint | Queries | Cold ms |
|---|---:|---:|
| `admin/dashboard` | 17 → 17 | 8,455 → **62** |
| `faculty/dashboard` | 1,024 → **5** | 1,306 → **10** |
| `admin/allocation-detail` | 386 → **11** | 750 → **25** |
| `faculty/allocation-detail` | 389 → **14** | 659 → **24** |
| `faculty/assessment-detail` | 386 → **11** | 442 → **17** |
| `faculty/lecture-detail` | 384 → **9** | 414 → **14** |
| `student/enrollment-detail` | 269 → **19** | 591 → **34** |
| `admin/allocations?search` | 6 → 6 | 684 → **13** |
| `admin/enrollments` | 37 → **7** | 585 → **19** |
| `admin/student-list` | 66 → **7** | 112 → **84** |
| `admin/faculty-list` | 45 → **6** | 104 → **17** |
| `student/enrollments` | 126 → **16** | 275 → **37** |

And the one that never worked at all:

| | before | after |
|---|---:|---:|
| `admin/enrollments?student` fill | > 60 s | **21 ms** |
| `admin/enrollments?faculty` fill | > 60 s | **161 ms** |
| `admin/allocations?semester` fill | 2,151 ms | **0 ms** |
| `admin/allocations?faculty` fill | 3,541 ms | **21 ms** |

A 0 ms fill means the key was already present on the harness's first poll, not
that a rebuild is instant — the scoped task now writes one key instead of one
for every semester and every teacher.

### 1.1 `admin/dashboard` — 8,455 ms to 62 ms

One query was 7,179 ms of it. Three independent one-to-many joins hung off
`department`, so MySQL built their cartesian product — students × faculty ×
programs per department, about 40,000 rows each — and `COUNT(DISTINCT ...)`
de-duplicated it afterwards, for three individually trivial numbers.

Now one correlated subquery per relation, each counting on its own index.

Two details worth recording. A fourth annotation, `enrollment_count`, was
computed over 75,000 rows and then dropped, because it was never named in the
`values()` output — it is deleted. And the old query's `GROUP BY` was sorting
the result rows as a side effect; the subquery version has no `GROUP BY`, so
the ordering had to be made explicit or the payload would have quietly changed
order.

### 1.2 `faculty/dashboard` — 1,024 queries to 5

1,000 of them were `SELECT result WHERE enrollment_id = ?`, one per enrollment
across the teacher's classes, from a Python loop averaging marks.

Replaced with one aggregate per completed allocation. `enrollment → result` is
one-to-one so the join cannot fan out, and enrollments with no result
contribute NULL, which `SUM` skips — which is what the old `hasattr` guard did.
The three separate `.count()` calls became one aggregate, and the
`prefetch_related` became `select_related`, since every use of it was a
filtered count that re-queries and ignores a prefetch cache anyway.

### 1.3 The detail endpoints — ~390 queries each to 9–19

Four endpoints serialised a whole class one student at a time: 125 × `Student`,
125 × `person`, 125 × `result` on the allocation details, and the same walk
through `obj.enrollment.student.student_id` on the assessment and lecture
details. One prefetch each.

`student/enrollment-detail` was slightly different — 251 of its 269 queries
were `SELECT enrollment WHERE enrollment_id = ?`, from
`AssessmentCheckedHyperlinkedIdentityField.get_url` reading
`obj.enrollment.enrollment_id`: fetching the parent row to read an id already
sitting on the child. The guard two lines above it already used
`obj.assessment_id` correctly.

### 1.4 `admin/allocations?search` — 684 ms to 13 ms

645 ms of it was two correlated `EXISTS` subqueries, because `search_fields`
crossed a reverse relation into `enrollment`. That field is gone: you look a
student up on the student list, not on the allocation list.

### 1.5 `admin/student-list` — 66 queries to 7

`cache_student_data_task` already fed this serializer a fully prefetched
queryset. The view's own queryset was bare. So `?search` and `?ordering`, the
two paths that never consult the cache, were the only paths not getting it —
and they are the ones admins use most.

### 1.6 `cache_enrollment_data_task` — the rebuild that never finished

The worst thing the audit found, and the least visible.

The task loaded all 75,000 enrollments, serialised them twice in Python, and
wrote about 5,200 keys. It was fired from `perform_create`, `perform_update`
and `perform_destroy`, so **one student enrolling in one course rebuilt the
cache for every student and every teacher in the system.** Both enrollment
filter endpoints reported `fill > 60s`: the cache never arrived, cost three CPU
cores for minutes, and returned nothing. During the first audit run this task
alone saturated the worker and starved every job behind it.

It now takes optional `student_ids` / `faculty_ids` and rebuilds only those.
Omitting both keeps the full rebuild for callers that want it. Each write path
passes the keys it actually affects — `perform_update` passes the old owners as
well, since an enrollment can move to another allocation and leave the previous
teacher's key wrong. The list view's cache miss passes the single key it
missed.

`str(Student)` and `str(Faculty)` both return `person_id`, which is also what
the view reads out of the query param, so the key names did not change.

Two smaller things in the same task. Its queryset was a bare
`Enrollment.objects.all()` behind a serializer that reads
`obj.student.student_id` and `obj.result` per row — the same N+1 as everywhere
else, running 75,000 times. And each key was deleted before being rewritten, so
during a rebuild the cache was not stale but *absent*, and every reader fell
through to the database. Writing over the key closes that window.

One deliberate departure from the original recommendation: on delete it
suggested removing a student's key when their last enrollment goes. An empty
list is cached instead. Empty is the true answer, and the view treats only
`None` as a miss — a deleted key would miss forever and re-fire a rebuild on
every read.

### 1.7 `admin/enrollments` — 585 ms to 19 ms

This was mislabelled in the first version of this report as an index problem.
It was not. It was two separate faults, both on the database path, and neither
had anything to do with the cache work in 1.6 — the unfiltered list never
consults a cache at all.

**A runaway ORDER BY.** `Enrollment.Meta.ordering` was:

```python
ordering = ['enrollment_id', 'student', 'allocation']
```

`student` and `allocation` are foreign keys. Ordering by a foreign key makes
Django substitute *that model's* `Meta.ordering`, recursively — so `student`
expanded into the person's names, the class batch year and the admission date,
and `allocation` expanded into the teacher's names, department, designation and
semester. The statement that came out had **7 joins and a 14-column ORDER BY**
to return 10 rows, and MySQL had to build and sort the joined set before it
could apply the `LIMIT`. That one query was 477 ms.

All of it was dead weight: the first sort key is `enrollment_id`, the primary
key, so nothing after it could ever break a tie. `ordering = ['enrollment_id']`
gives 0 joins and 1 sort term in the same row order — confirmed by comparing
the old and new ordering's output directly, not just asserted.

The same shape existed in four more models, found by checking for it as this
report originally recommended. `Faculty`, `Student` and `Admin` all led their
ordering with a `OneToOneField` to `Person` that is also their own primary key
— still a foreign key as far as Django's ordering expansion is concerned, so
each pulled in `person`'s own name columns for a tiebreak that could never
fire. Fixed with `ordering = ['pk']`, which sorts on the same raw column
without the join. `CourseAllocation` led with its own `AutoField` PK exactly
like `Enrollment`, with the identical two dead FK columns behind it — fixed
the same way. All four verified byte-for-byte identical row order, old
ordering vs new, against real data.

`Meta.ordering` is model state, so the five changes need a migration —
`0020_alter_admin_options_...`. It contains only `AlterModelOptions`, which
emits no SQL at all: `sqlmigrate Models 0020` prints `(no-op)` for every
operation, so no table is touched and no reseed was needed.

**An N+1.** The remaining 30 of 37 queries were 10 × `Student`, 10 × `person`,
10 × `result` for a page of ten. Fixed with the same `select_related` as
everywhere else.

### 1.8 The other cache tasks rebuilt everything

Section 1.6 fixed enrollment, which was where it hurt most, because it is the
largest table. The identical pattern was in the student, faculty, course and
semester tasks too — each now takes the same kind of optional scoping
parameter (a set of group keys the write actually touched, not just an id),
because unlike enrollment's per-student/per-faculty keys, these caches are
also partitioned by department, program, class, designation and status. A
write passes both the old and new group on an update, since a row can move
between groups in one write.

Only one of the four is visible in this benchmark. `cache_courseAllocation_data_task`
is the one other task whose *list-view* cache-miss branch was also scoped
(the miss branch for `admin/student-list`, `admin/faculty-list` and
`admin/semesters` always checks the *unfiltered* list key first and fires a
full rebuild regardless of which filter was requested, so the harness can only
ever exercise their full-rebuild path):

| Endpoint | Fill before | Fill after |
|---|---:|---:|
| `admin/allocations?semester` | 2,151 ms | **1 ms** |
| `admin/allocations?faculty` | 3,541 ms | **1 ms** |

The student, faculty and semester fixes only pay off on the write paths this
benchmark doesn't measure (`perform_create`/`perform_update`/`perform_destroy`)
— verified instead by calling each task directly against real data and
confirming the scoped call writes the same content the full rebuild would have
written to that one key, plus the full 779-test suite passing throughout.

### 1.9 `admin/faculty-list` — 45 queries to 6

Exactly 1.5 again, one model over. The view's queryset was
`Faculty.objects.all()` while the serializer read each row's person, user,
address and qualifications — four extra queries per row. Meanwhile
`cache_faculty_data_task` was already building the fully prefetched version of
the same queryset for the cache. So the cached branches were fine and only
`?search` and `?ordering`, which never consult the cache, paid the 45. Those
are the branches admins use most. The view now uses the same queryset the
cache task does.

All six variants moved together: 45 → 6 queries (46 → 7 with a filter), and
roughly 100 ms → 15 ms.

### 1.10 `student/enrollments` — 126 queries to 16

Two independent faults, and the second is the more interesting one.

**The fetch stopped short of what the serializer reads.** `get_queryset` had
`select_related('result', 'allocation')`, but `StudentCourseAllocationSerializer`
then walks `allocation → faculty → employee_id` for the teacher's name and
`allocation → course → pre_requisite` for the course box. Three to four
queries per enrollment. The `select_related` now spans the whole chain.

**A prefetch was built and then discarded.** The view already did the right
thing:

```python
prefetch_related(Prefetch('allocation__assessment_set__assessmentchecked_set',
                          queryset=AssessmentChecked.objects.filter(...)))
```

and `StudentAssessmentSerializer.to_representation` then ignored it, calling
`instance.assessmentchecked_set.filter(...).first()`. Adding any filter to a
prefetched manager abandons the cache and issues a fresh query, so this ran
once per assessment per enrollment — 40 queries on a page of ten, on top of
the prefetch, which still ran and was thrown away.

The serializer now reads the prefetched rows when they are present:

```python
if 'assessmentchecked_set' in getattr(instance, '_prefetched_objects_cache', {}):
    checked = next(iter(instance.assessmentchecked_set.all()), None)
else:
    checked = instance.assessmentchecked_set.filter(...).first()
```

The fallback is not optional. `StudentEnrollmentRetrieveView` shares this
serializer and has no such prefetch, and a bare `.all()` on an `Assessment`
returns every student's checked row, not the requesting student's — which is
precisely what the existing filter is there to prevent. The original code is
correct; it is only slow. The retrieve view keeps taking the filter branch and
is unchanged at 19 queries.

Verified by rendering the page through both branches on identical data — 40
assessments, all with real checked rows — and comparing the JSON byte for
byte.

The retrieve view could take the same `Prefetch` and let the fallback go. What
it must *not* take is the list view's other clause,
`allocation__semester__status__in=['Active', 'Completed']`: the retrieve view
deliberately has no such filter, and copying it would 404 an enrollment whose
semester has not activated yet.

---

## 2. What is still open

Nothing from the original audit. Two decisions remain, both in section 3.

---

## 3. Things that got slower

Two endpoints gained a query. Both are mine, and the query counts are exact:

| Endpoint | Queries | Cold ms |
|---|---|---:|
| `faculty/lectures` | 14 → **15** | 33 → 57 |
| `faculty/assessments` | 9 → **10** | 26 → 19 |

The prefetches added in 1.3 went onto the list views as well as the detail
views, which costs one extra query to fetch the nested rows for the whole page.
On the detail endpoints that trades ~375 queries for one and is obviously
right. On these two list endpoints there were few nested rows to begin with, so
it is one query bought for nothing.

Ignore the milliseconds here — one of them now reads *faster* than before and
the other slower, which is the machine moving, not the code. The honest
statement is the query column: two endpoints each pay one extra query.

Worth reverting on the list views specifically, or worth keeping as insurance
against a class with more data. Either way it should be a decision, not an
accident. **Still undecided.**

The other open decision is in 1.10: whether `StudentEnrollmentRetrieveView`
should take the same `Prefetch` as the list view, which would let the fallback
branch in `StudentAssessmentSerializer.to_representation` be deleted.

Three endpoints that earlier drafts listed here — `admin/classes`,
`student/attendance` and `admin/semesters` — have been removed. Their query
counts never changed, and on the latest run all three read *faster* than the
original baseline. They were never regressions, only noise.

---

## 4. Method

Median of 5 runs per endpoint. Each endpoint: purge the queue and wait for the
worker to go idle, one discarded warm-up request, then five rounds of
`cache.clear()` → cold request → wait for the key → warm request.

The **fill** column is the one that matters operationally: after any write,
that is how long every user keeps hitting the database. It is measured against
a real worker (`celery-worker-test`) on its own broker.

Query counts come from `CaptureQueriesContext`, which works without
`DEBUG=True`; turning `DEBUG` on would itself change what is being measured.

Read the query counts as exact — they were identical across runs. Read the
millisecond figures as magnitudes only, and do not compare them between runs
taken hours apart.

That last point is stronger than the usual "timings are noisy" caveat, and it
is measured rather than assumed. Between the final two runs the only code
change was two fixes touching seven endpoints. Yet the **warm** total across
all 65 fell from 2,364 ms to 1,082 ms. A warm request reads its answer from
Redis and runs the same two queries every time; nothing in those two fixes can
reach it. Worker fill time fell the same way, 33 s to 21 s, on tasks that were
not touched in that round, and the benchmark's own wall clock has drifted
6 m 41 s → 4 m 48 s while the full test suite went 5 m 32 s → 4 m 14 s.

The cause is almost certainly MySQL's InnoDB buffer pool warming across a day
of repeated runs against the same stress dataset. The practical consequence:
of the last run's 1,980 ms improvement, about 740 ms sits on the seven
endpoints whose query counts actually changed, and about 1,240 ms sits on 23
endpoints whose query counts did not change at all. Only the first number
belongs to this work.

Rows measured while the worker is rebuilding a cache are pessimistic on top of
that, since the worker and the request share CPU. That contention is real in
production too.

Causes are not inferred from the counts. They come from
`test_query_profile.py`, which collapses every captured statement to its shape
and counts the repeats.

### A correction to the first run

The first version of this audit was wrong and its numbers should not be used.

The root `conftest.py` sets `CELERY_TASK_ALWAYS_EAGER = True` as an autouse
fixture. Five admin list views answer a cache miss by publishing a
cache-rebuild job and returning. Under eager execution that job ran **inline,
inside the request being timed** — so every "cold" figure for those endpoints
was the request plus a full cache rebuild, work production does on a worker.
`admin/student-list` read 342 q / 6,525 ms when it is actually 66 q / 112 ms.

The tell was visible in the first table and missed: three different
query-param branches of `admin/faculty-list` all reported an identical 149
queries cold. Three different code paths cannot agree to the query unless the
dominant cost is the same param-independent thing in all of them.

`tests/benchmarks/conftest.py` now shadows that fixture for this directory only, and
the audit runs against a real worker.
