"""
Where the queries actually go, for the endpoints the audit flagged.

`results.md` says *how many* queries an endpoint runs; this says *which*, by
collapsing every captured SQL statement to its shape (literals stripped) and
counting the repeats. A shape that appears hundreds of times is an N+1 and
names the relation that needs `select_related`/`prefetch_related`.

Run with:
    docker compose exec backend pytest tests/benchmarks/test_query_profile.py -m benchmark -q -s
"""
import re
from collections import Counter

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.benchmark

PROFILE = [
    ('student/enrollments',     'student', '/api/student/enrollments/'),
    ('admin/student-list',      'admin',   '/api/admin/students/'),
    ('admin/semesters',         'admin',   '/api/admin/semesters/'),
    ('faculty/dashboard',       'faculty', '/api/faculty/dashboard/'),
    ('admin/dashboard',         'admin',   '/api/admin/dashboard/'),
    ('admin/allocation-detail', 'admin',   '/api/admin/allocations/{allocation}/'),
    ('admin/enrollments',       'admin',   '/api/admin/enrollments/'),
    ('faculty/allocation-detail','faculty', '/api/faculty/allocations/{allocation}/'),
    ('faculty/lecture-detail',  'faculty', '/api/faculty/allocations/{allocation}/lectures/{lecture}/'),
    ('student/enrollment-detail','student', '/api/student/enrollments/{student_enrollment}/'),
    ('admin/allocations?search','admin',   '/api/admin/allocations/?search=CRS-0001'),
]


def _shape(sql):
    """Collapse a statement to its structure so repeats group together."""
    sql = re.sub(r"'[^']*'", "'?'", sql)
    sql = re.sub(r'\b\d+\b', '?', sql)
    sql = re.sub(r'\(\s*(\?\s*,\s*)+\?\s*\)', '(?...)', sql)
    return re.sub(r'\s+', ' ', sql).strip()


@pytest.mark.parametrize('name,role,template', PROFILE, ids=[p[0] for p in PROFILE])
def test_query_shapes(name, role, template, bench_ids, bench_admin_client,
                      bench_faculty_client, bench_student_client):
    client = {'admin': bench_admin_client, 'faculty': bench_faculty_client,
              'student': bench_student_client}[role]
    url = template.format(**bench_ids)

    client.get(url)          # warm-up
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)

    assert response.status_code < 400
    shapes = Counter(_shape(q['sql']) for q in ctx.captured_queries)

    print(f'\n\n=== {name} — {len(ctx.captured_queries)} queries, '
          f'{len(shapes)} distinct shapes ===')
    print('-- most repeated (N+1 candidates) --')
    for sql, count in shapes.most_common(4):
        print(f'  {count:>5d} x  {sql[:600]}')

    # A low query count with a high latency is a different problem: one
    # expensive statement, not a loop. Rank by total time spent per shape.
    by_time = {}
    for q in ctx.captured_queries:
        by_time[_shape(q['sql'])] = by_time.get(_shape(q['sql']), 0.0) + float(q['time'])
    print('-- slowest by total time --')
    for sql, secs in sorted(by_time.items(), key=lambda kv: -kv[1])[:4]:
        print(f'  {secs * 1000:>8.1f} ms  ({shapes[sql]}x)  {sql[:400]}')
