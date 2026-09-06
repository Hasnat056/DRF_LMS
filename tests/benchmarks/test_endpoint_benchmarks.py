"""
Endpoint performance audit — query counts and latency, cold and warm.

Excluded from normal runs by `-m "not benchmark"` in pytest.ini. To run:

    docker compose -f tests/docker-compose.yaml up -d
    docker compose -f tests/docker-compose.yaml \
        run --rm test-runner pytest tests/benchmarks/ -m benchmark -q -s

Method, per endpoint:

    warm-up request (discarded — a cold process reads ~10x its steady state)
    repeat 5x:
        cache.clear()  -> request -> record (queries, ms)   # cold
        await fill     ->                    record (ms)    # worker latency
                       -> request -> record (queries, ms)   # warm
    report the median of each

The `await fill` step exists because the Celery worker is real
(`celery-worker-test`, not eager). Five admin list views answer a cache miss by
publishing a job and returning immediately; the cache is filled a moment later
on the worker. Sampling "warm" straight after "cold" would just catch a second
miss and report every cached list as uncached. Waiting for the key also turns
the wait itself into the number that matters operationally: how long an admin
keeps paying the slow path after any write invalidates the cache.

Endpoints whose cache is written synchronously by the view (`admin/dashboard`,
`admin/profile`, the faculty and student keys) carry no key here and need no
wait — their warm sample is already a hit.

Medians rather than means: a containerised MySQL throws the occasional 100 ms
outlier that would drag an average somewhere meaningless.

Query counting uses `CaptureQueriesContext`, which works without DEBUG=True —
flipping DEBUG on would itself change what is being measured (it installs a
query logger and disables some short-circuits).

Only GET endpoints are covered. Writes mutate state, so repeated runs are not
comparable, and none of them have a cache to hit.
"""
import statistics
import time
from pathlib import Path

import pytest
from django.core.cache import cache
from django.db import connection, reset_queries

from Models.models import AcademicSession, Semester
from NexusAPI.celery import app as celery_app
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.benchmark

REPEATS = 5
RESULTS_PATH = Path(__file__).resolve().parent / 'results.md'

# Collected across the parametrised runs, written out by `report` at teardown.
_records = []


# ---------------------------------------------------------------------------
# Endpoint catalogue
#
#   (id, client fixture, url template, query params, cache key)
#
# `{name}` placeholders in the template and the key are filled from the
# `bench_ids` fixture.
#
# The cache key is the key the request should be *served from*, and it is only
# set where a Celery task fills it — those are the rows that need the harness to
# wait for the worker. `None` means either no cache or a cache the view writes
# synchronously, and the warm sample is taken immediately.
#
# List endpoints are measured on three kinds of branch, because the views treat
# them completely differently (AdminModule/views.py):
#
#   plain           — served whole from the entity's list key
#   filtered        — served from a per-combination key, e.g. admin:faculty:department:D00
#   search/ordering — `return super().list(...)` unconditionally, no cache read
#                     at all. Arbitrary search strings cannot be cached, so this
#                     branch is a permanent DB hit by design; it is measured as a
#                     floor, not as a cache result.
# ---------------------------------------------------------------------------

ENDPOINTS = [
    # -- public ------------------------------------------------------------
    ('public/current-session',      'anon',    '/api/sessions/current/', None, None),

    # -- admin: people -----------------------------------------------------
    ('admin/profile',               'admin',   '/api/admin/profile/', None, None),
    ('admin/dashboard',             'admin',   '/api/admin/dashboard/', None, None),

    ('admin/faculty-list',          'admin',   '/api/admin/faculty/', None, 'admin:faculty_list'),
    ('admin/faculty-list?dept',     'admin',   '/api/admin/faculty/', {'department': 'D00'}, 'admin:faculty:department:D00'),
    ('admin/faculty-list?desig',    'admin',   '/api/admin/faculty/', {'designation': 'Professor'}, 'admin:faculty:designation:Professor'),
    ('admin/faculty-list?dept+desig', 'admin', '/api/admin/faculty/', {'department': 'D00', 'designation': 'Professor'}, 'admin:faculty:D00:Professor'),
    ('admin/faculty-list?search',   'admin',   '/api/admin/faculty/', {'search': 'Faculty1'}, None),
    ('admin/faculty-list?ordering', 'admin',   '/api/admin/faculty/', {'ordering': 'employee_id__first_name'}, None),
    ('admin/faculty-detail',        'admin',   '/api/admin/faculty/{faculty_employee_id}/', None, None),

    ('admin/student-list',          'admin',   '/api/admin/students/', None, 'admin:student_list'),
    ('admin/student-list?dept',     'admin',   '/api/admin/students/', {'program__department': 'D00'}, 'admin:students:department:D00'),
    ('admin/student-list?status',   'admin',   '/api/admin/students/', {'status': 'Active'}, 'admin:students:status:Active'),
    ('admin/student-list?dept+status', 'admin', '/api/admin/students/', {'program__department': 'D00', 'status': 'Active'}, 'admin:students:D00:Active'),
    # views.py:417 reads `admin:students:program:{query_params.get("program_id")}`
    # but the filterset field is `program`, so the lookup is always
    # `...:program:None` while the task writes `...:program:P000`. Measured to
    # confirm the key can never hit.
    ('admin/student-list?program',  'admin',   '/api/admin/students/', {'program': 'P000'}, 'admin:students:program:P000'),
    # These two were swapped until the views.py fix: the branch keyed on the
    # undocumented `class_` while `student_class` is the real filterset field.
    # Now `student_class` is the cached branch and `class_` filters nothing and
    # reads no cache. Both kept, so the old confusion stays visible.
    ('admin/student-list?class_',   'admin',   '/api/admin/students/', {'class_': '{class}'}, None),
    ('admin/student-list?student_class', 'admin', '/api/admin/students/', {'student_class': '{class}'}, 'admin:students:class:{class}'),
    ('admin/student-list?search',   'admin',   '/api/admin/students/', {'search': 'Student1'}, None),
    ('admin/student-detail',        'admin',   '/api/admin/students/{student_id}/', None, None),

    # -- admin: structure --------------------------------------------------
    ('admin/departments',           'admin',   '/api/admin/departments/', None, None),
    ('admin/department-detail',     'admin',   '/api/admin/departments/{department}/', None, None),
    ('admin/programs',              'admin',   '/api/admin/programs/', None, 'admin:programs_list'),
    ('admin/programs?dept',         'admin',   '/api/admin/programs/', {'department': 'D00'}, 'admin:programs:department:D00'),
    ('admin/program-detail',        'admin',   '/api/admin/programs/{program}/', None, None),
    ('admin/courses',               'admin',   '/api/admin/courses/', None, 'admin:courses_list'),
    ('admin/courses?search',        'admin',   '/api/admin/courses/', {'search': 'Course 1'}, None),
    ('admin/course-detail',         'admin',   '/api/admin/courses/{course}/', None, None),
    ('admin/classes',               'admin',   '/api/admin/classes/', None, None),
    ('admin/class-detail',          'admin',   '/api/admin/classes/{class}/', None, None),

    # -- admin: lifecycle --------------------------------------------------
    ('admin/sessions',              'admin',   '/api/admin/sessions/', None, None),
    ('admin/session-detail',        'admin',   '/api/admin/sessions/{session}/', None, None),
    ('admin/semesters',             'admin',   '/api/admin/semesters/', None, 'admin:semesters_list'),
    ('admin/semesters?class',       'admin',   '/api/admin/semesters/', {'associated_class': '{class}'}, 'admin:semesters:class:{class}'),
    ('admin/semester-detail',       'admin',   '/api/admin/semesters/{semester}/', None, None),
    ('admin/allocations',           'admin',   '/api/admin/allocations/', None, None),
    ('admin/allocations?semester',  'admin',   '/api/admin/allocations/', {'semester': '{semester}'}, 'admin:allocations:semester:{semester}'),
    ('admin/allocations?faculty',   'admin',   '/api/admin/allocations/', {'faculty': '{faculty_employee_id}'}, 'admin:allocations:faculty:{faculty_employee_id}'),
    ('admin/allocations?search',    'admin',   '/api/admin/allocations/', {'search': 'CRS-0001'}, None),
    ('admin/allocation-detail',     'admin',   '/api/admin/allocations/{allocation}/', None, None),
    ('admin/allocations-bulk',      'admin',   '/api/admin/allocations/bulk/', None, None),
    ('admin/enrollments',           'admin',   '/api/admin/enrollments/', None, None),
    ('admin/enrollments?student',   'admin',   '/api/admin/enrollments/', {'student': '{student_id}'}, 'admin:enrollments:student:{student_id}'),
    ('admin/enrollments?faculty',   'admin',   '/api/admin/enrollments/', {'allocation__faculty': '{faculty_employee_id}'}, 'admin:enrollments:faculty:{faculty_employee_id}'),
    ('admin/enrollment-detail',     'admin',   '/api/admin/enrollments/{student_enrollment}/', None, None),
    ('admin/transcripts',           'admin',   '/api/admin/transcripts/', None, None),
    ('admin/requests',              'admin',   '/api/admin/requests/', None, None),

    # -- faculty -----------------------------------------------------------
    ('faculty/profile',             'faculty', '/api/faculty/profile/', None, None),
    ('faculty/dashboard',           'faculty', '/api/faculty/dashboard/', None, None),
    ('faculty/allocations',         'faculty', '/api/faculty/allocations/', None, None),
    ('faculty/allocation-detail',   'faculty', '/api/faculty/allocations/{allocation}/', None, None),
    ('faculty/assessments',         'faculty', '/api/faculty/allocations/{allocation}/assessments/', None, None),
    ('faculty/assessment-detail',   'faculty', '/api/faculty/allocations/{allocation}/assessments/{assessment}/', None, None),
    ('faculty/lectures',            'faculty', '/api/faculty/allocations/{allocation}/lectures/', None, None),
    ('faculty/lecture-detail',      'faculty', '/api/faculty/allocations/{allocation}/lectures/{lecture}/', None, None),
    ('faculty/requests',            'faculty', '/api/faculty/requests/', None, None),

    # -- student -----------------------------------------------------------
    ('student/profile',             'student', '/api/student/profile/', None, None),
    ('student/dashboard',           'student', '/api/student/dashboard/', None, None),
    ('student/enrollments',         'student', '/api/student/enrollments/', None, None),
    ('student/enrollment-detail',   'student', '/api/student/enrollments/{student_enrollment}/', None, None),
    ('student/attendance',          'student', '/api/student/attendance/', None, None),
    ('student/attendance-detail',   'student', '/api/student/attendance/{student_enrollment}/', None, None),
    ('student/transcripts',         'student', '/api/student/transcripts/', None, None),
    ('student/reviews',             'student', '/api/student/{student_id}/enrollments/reviews/', None, None),

    # -- notifications -----------------------------------------------------
    ('notifications/list',          'student', '/api/notifications/', None, None),
    ('notifications/unread-count',  'student', '/api/notifications/unread-count/', None, None),
]


# ---------------------------------------------------------------------------
# Per-endpoint staging
#
# A couple of endpoints only do real work in a lifecycle phase the seeded
# dataset is not in. Only one session may be live at a time
# (AdminModule/serializers.py:1409), so the stress data cannot hold both a
# term in progress and a term being set up. These run inside the benchmark's
# own rollback transaction, so nothing leaks to the next test.
# ---------------------------------------------------------------------------

def _stage_bulk_worksheet(ids):
    """Put the live session back into the setup phase.

    `BulkCourseAllocationAPIView` resolves a session by status Initiated or
    Available and builds its skeleton from Inactive semesters — with the
    seeded session Active it short-circuits to an empty worksheet and measures
    nothing but authentication.
    """
    AcademicSession.objects.filter(pk=ids['session']).update(status='Initiated')
    Semester.objects.filter(session_id=ids['session']).update(status='Inactive')


SETUPS = {
    'admin/allocations-bulk': _stage_bulk_worksheet,
}


# Generous: the worker rebuilds every filter combination for an entity, and
# cache_student_data_task walks 5 departments x 4 statuses, 10 programs and
# 40 classes over 5,000 students. A key that has not appeared by then is not
# slow, it is wrong.
FILL_TIMEOUT_S = 60.0
_FILL_POLL_S = 0.02


def _sample(client, url, params):
    """One request; returns (query_count, elapsed_ms, status_code)."""
    reset_queries()
    with CaptureQueriesContext(connection) as ctx:
        started = time.perf_counter()
        response = client.get(url, params) if params else client.get(url)
        elapsed = (time.perf_counter() - started) * 1000
    return len(ctx.captured_queries), elapsed, response.status_code


def _drain_worker(timeout=300.0):
    """Empty the queue and wait for the worker to finish what it is holding.

    Without this the endpoints contaminate each other. cache_enrollment_data_task
    serialises all 75,000 enrollments twice per run, entirely in Python, and one
    cold request fires another; they pile up, saturate every worker slot and then
    compete for CPU with the request being timed. A run with that backlog read
    admin/dashboard at 11,219 ms against 7,193 ms on a quiet worker.
    """
    celery_app.control.purge()
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        active = celery_app.control.inspect(timeout=1.0).active() or {}
        if not any(active.values()):
            return True
        time.sleep(0.5)
    return False


def _await_fill(key):
    """Block until `celery-worker-test` has written `key`.

    Returns milliseconds waited; `None` where the endpoint has no worker-filled
    key; `inf` if the key never appeared. That last case is a finding rather
    than a timeout: it means the key the view reads back is not the key the
    task writes, so the branch can never serve from cache.
    """
    if key is None:
        return None
    started = time.perf_counter()
    while time.perf_counter() - started < FILL_TIMEOUT_S:
        if cache.get(key) is not None:
            return (time.perf_counter() - started) * 1000
        time.sleep(_FILL_POLL_S)
    return float('inf')


@pytest.mark.parametrize(
    'name,role,template,params,key', ENDPOINTS, ids=[e[0] for e in ENDPOINTS]
)
def test_endpoint_performance(
    name, role, template, params, key, bench_ids,
    bench_admin_client, bench_faculty_client, bench_student_client,
    bench_anon_client,
):
    client = {
        'admin': bench_admin_client,
        'faculty': bench_faculty_client,
        'student': bench_student_client,
        'anon': bench_anon_client,
    }[role]
    url = template.format(**bench_ids)
    if params:
        params = {k: str(v).format(**bench_ids) for k, v in params.items()}
    if key:
        key = key.format(**bench_ids)

    # Start from a quiet worker, so this endpoint's numbers are its own.
    _drain_worker()

    stage = SETUPS.get(name)
    if stage:
        stage(bench_ids)

    # Discarded: the first request through a given view pays for URL
    # resolution, serializer construction and connection warm-up. Measured
    # cold-start on one endpoint today read 114 ms against a warmed 12 ms.
    _, _, status = _sample(client, url, params)

    cold_q, cold_ms, warm_q, warm_ms, fill_ms = [], [], [], [], []
    for _ in range(REPEATS):
        cache.clear()
        q, ms, status = _sample(client, url, params)
        cold_q.append(q)
        cold_ms.append(ms)

        # The cold request published a job and returned. Without this wait the
        # "warm" sample below is just a second miss.
        filled = _await_fill(key)
        if filled is not None:
            fill_ms.append(filled)

        q, ms, _ = _sample(client, url, params)
        warm_q.append(q)
        warm_ms.append(ms)

    record = {
        'name': name,
        'role': role,
        'url': url,
        'key': key,
        'status': status,
        'cold_q': int(statistics.median(cold_q)),
        'warm_q': int(statistics.median(warm_q)),
        'cold_ms': statistics.median(cold_ms),
        'warm_ms': statistics.median(warm_ms),
        'fill_ms': statistics.median(fill_ms) if fill_ms else None,
    }
    _records.append(record)

    fill = record['fill_ms']
    fill_txt = '     -  ' if fill is None else (
        '  never ' if fill == float('inf') else f'{fill:>8.0f}'
    )
    print(
        f"\n{name:34s} {status}  "
        f"q {record['cold_q']:>4d}/{record['warm_q']:<4d}  "
        f"ms {record['cold_ms']:>8.1f}/{record['warm_ms']:<8.1f}  "
        f"fill {fill_txt}"
    )

    # A benchmark that measured an error page measures nothing. 404 is a real
    # failure of the catalogue above, not of the endpoint.
    assert status < 400, f'{name} -> {url} returned {status}'


def _classify(r):
    """One-word verdict per row, so the table reads without cross-referencing."""
    if r['fill_ms'] == float('inf'):
        # Not necessarily a wrong key: cache_enrollment_data_task rebuilds all
        # 75,000 enrollments and writes its faculty keys only after 5,000
        # student ones, so it can simply outlast the wait.
        return f'fill > {FILL_TIMEOUT_S:.0f}s'
    if r['warm_q'] < r['cold_q']:
        saved = r['cold_ms'] - r['warm_ms']
        return 'cached' if saved > 5 else 'cached, marginal'
    if r['key'] is not None:
        # Worker filled the key, yet the warm request still queries the same.
        return 'cache not used'
    return 'no cache'


def _fmt_fill(v):
    if v is None:
        return '—'
    if v == float('inf'):
        return f'**&gt;{FILL_TIMEOUT_S:.0f}s**'
    return f'{v:.0f}'


@pytest.fixture(scope='module', autouse=True)
def report():
    yield
    if not _records:
        return
    rows = sorted(_records, key=lambda r: r['cold_ms'], reverse=True)
    lines = [
        '# Endpoint performance audit',
        '',
        f'{len(rows)} GET endpoints, median of {REPEATS} runs against the stress',
        'dataset.',
        '',
        '- **Cold** — the first request after `cache.clear()`.',
        '- **Fill** — how long after that request the Celery worker finished',
        '  writing the key, measured with a real worker (`celery-worker-test`),',
        '  not eager execution. This is how long an admin keeps paying the cold',
        '  path after any write invalidates the cache. `—` means no',
        '  worker-filled key; **never** means the key the view reads back is not',
        '  the key the task writes.',
        '- **Warm** — the request after the key was confirmed present.',
        '',
        'Rows ending `?search` / `?ordering` never consult the cache at all —',
        'those views `return super().list(...)` unconditionally, because an',
        'arbitrary search string is not cacheable. They are measured as the',
        'floor cost of the uncached path, not as a cache result.',
        '',
        'This file is regenerated on every run — the analysis lives in',
        '`FINDINGS.md`, which is not overwritten.',
        '',
        '| Endpoint | Role | Queries cold | Queries warm | Cold ms | Fill ms | Warm ms | Verdict |',
        '|---|---|---:|---:|---:|---:|---:|---|',
    ]
    for r in rows:
        lines.append(
            f"| `{r['name']}` | {r['role']} | {r['cold_q']} | {r['warm_q']} "
            f"| {r['cold_ms']:.1f} | {_fmt_fill(r['fill_ms'])} "
            f"| {r['warm_ms']:.1f} | {_classify(r)} |"
        )
    lines.append('')
    RESULTS_PATH.write_text('\n'.join(lines))
    print(f'\n\nwrote {RESULTS_PATH}\n')
    print('\n'.join(lines[5:]))
