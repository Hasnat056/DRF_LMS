"""
cache_pressure.py
-----------------
Does the admin list cache earn its keep under concurrent load?

The endpoint audit measured one request at a time and found the cache saved
165 ms across 27 endpoints while costing 19,657 ms of worker time to refill
after a single invalidation. But concurrency 1 is the cache's worst case: MySQL
is uncontended, so the database path looks as good as it ever will.

This runs the same endpoints under real load, and varies the one thing that
decides the answer -- **how often a write invalidates the cache**.

    arm A   readers only, nothing ever invalidates      WRITE_INTERVAL_S=0
    arm B   readers + one write a minute                WRITE_INTERVAL_S=60
    arm C   readers + one write every 10s               WRITE_INTERVAL_S=10
    arm D   readers + one write a second                WRITE_INTERVAL_S=1

If the cache earns its keep, p95 stays flat across the arms. If the audit's
finding holds, it degrades as the write rate rises, because every write queues
a multi-second rebuild that competes with the readers for the same CPU.

Run against the stress dataset, not the dev database:

    docker compose -f tests/docker-compose.yaml up -d
    WRITE_INTERVAL_S=0 locust -f tests/loadtest/cache_pressure.py \\
        --headless -u 50 -r 10 --run-time 3m --host http://localhost:8002 \\
        --csv results/arm_a

Then repeat with 60, 10 and 1, and compare the p95 columns.

Watch `vmstat 5` alongside. This machine has swung 2.6x from host memory
pressure; any run showing swap-in should be discarded, not interpreted.
"""
import os
import random

import requests
from locust import HttpUser, constant, events, task
from locust.exception import StopUser

# The stress seeder creates its users with `User(username=email)` and never
# sets a password -- the benchmark harness mints JWTs directly instead. So
# there is nothing to log in with, and LOAD_TOKEN is the normal path here:
#
#   export LOAD_TOKEN=$(docker compose -f tests/docker-compose.yaml \
#     exec -T backend-stress python manage.py shell -c "
#   from rest_framework_simplejwt.tokens import RefreshToken
#   from django.contrib.auth.models import User
#   print(RefreshToken.for_user(User.objects.get(
#       username='bench.admin@stress.test')).access_token)" | tr -d '\r\n')
#
# Tokens last 60 minutes by default, which covers a set of arms. Username and
# password are kept as a fallback for a stack whose admin does have one.
LOAD_TOKEN = os.getenv('LOAD_TOKEN')

ADMIN_CREDENTIALS = {
    'username': os.getenv('LOAD_ADMIN_USER', 'bench.admin@stress.test'),
    'password': os.getenv('LOAD_ADMIN_PASS', ''),
}

BASE = '/api/admin'

# Seconds between cache-invalidating writes. 0 disables the writer entirely,
# which is arm A -- the read-only case the existing locustfile already covers.
WRITE_INTERVAL_S = float(os.getenv('WRITE_INTERVAL_S', '0'))

_token = None
_write_target = None          # (student_id, current_status)


@events.test_start.add_listener
def _setup(environment, **kwargs):
    """Authenticate once, and find a student the writer can safely re-save."""
    global _token, _write_target
    host = environment.host.rstrip('/')

    if LOAD_TOKEN:
        _token = LOAD_TOKEN
        print('[setup] using LOAD_TOKEN')
    else:
        r = requests.post(f'{host}/api/token/', json=ADMIN_CREDENTIALS, timeout=10)
        if r.status_code != 200:
            print(f'[setup] login failed: {r.status_code} {r.text[:200]}\n'
                  f'[setup] the stress users have no password -- set LOAD_TOKEN '
                  f'(see the note at the top of this file)')
            return
        _token = r.json().get('access')

    if WRITE_INTERVAL_S <= 0:
        print('[setup] WRITE_INTERVAL_S=0 -- arm A, no writer will spawn')
        return

    r = requests.get(
        f'{host}{BASE}/students/',
        headers={'Authorization': f'Bearer {_token}'}, timeout=30,
    )
    rows = r.json().get('results') or []
    if not rows:
        print('[setup] no students returned; writer disabled')
        return
    row = rows[0]
    # StudentSerializer nests the id inside `person`, and the detail route is
    # keyed on that person_id -- there is no top-level `student_id` field.
    student_id = (row.get('person') or {}).get('person_id')
    if not student_id:
        print(f'[setup] could not find person_id in {sorted(row)}; writer disabled')
        return
    _write_target = (student_id, row.get('status'))
    print(f'[setup] writer will re-save {_write_target[0]} '
          f'(status={_write_target[1]}) every {WRITE_INTERVAL_S}s')


class _Authenticated(HttpUser):
    abstract = True

    def on_start(self):
        if not _token:
            raise StopUser()
        self.client.headers.update({'Authorization': f'Bearer {_token}'})


class CachedListReader(_Authenticated):
    """Reads the admin lists that have a cache, weighted towards the ones the
    audit found to be slower warm than cold."""

    wait_time = constant(1)

    @task(5)
    def student_list(self):
        self.client.get(f'{BASE}/students/', name='GET /students/')

    @task(3)
    def student_by_status(self):
        self.client.get(f'{BASE}/students/?status=Active',
                        name='GET /students/?status')

    @task(3)
    def student_by_department(self):
        dept = random.choice(['D00', 'D01', 'D02', 'D03', 'D04'])
        self.client.get(f'{BASE}/students/?program__department={dept}',
                        name='GET /students/?department')

    @task(2)
    def student_by_program(self):
        program = random.choice([f'P00{n}' for n in range(5)])
        self.client.get(f'{BASE}/students/?program={program}',
                        name='GET /students/?program')

    @task(3)
    def faculty_list(self):
        self.client.get(f'{BASE}/faculty/', name='GET /faculty/')

    @task(2)
    def semesters(self):
        self.client.get(f'{BASE}/semesters/', name='GET /semesters/')

    @task(1)
    def student_search(self):
        """Never cached -- the floor cost of the database path, for contrast."""
        query = random.choice(['Student1', 'Student2', 'Ahmed', 'Ali'])
        self.client.get(f'{BASE}/students/?search={query}',
                        name='GET /students/?search (uncached)')


class CacheInvalidator(_Authenticated):
    """One user issuing the write that invalidates the student caches.

    It PATCHes a student's status to the value it already has. The write path
    runs in full and fires cache_student_data_task exactly as a real edit
    would, but no row actually changes -- so repeated runs do not drift the
    stress dataset the benchmark depends on.
    """

    fixed_count = 1
    wait_time = constant(WRITE_INTERVAL_S if WRITE_INTERVAL_S > 0 else 1)

    @task
    def resave_student(self):
        if not _write_target:
            raise StopUser()
        student_id, current_status = _write_target
        self.client.patch(
            f'{BASE}/students/{student_id}/',
            json={'status': current_status},
            name='PATCH /students/<id>/ (invalidates)',
        )


# Arm A must run with the SAME number of readers as the other arms, or its
# numbers are not comparable to theirs. `fixed_count = 0` does NOT mean "do not
# spawn": locust reads it as "not a fixed count, allocate by weight", and both
# classes then default to weight 1 -- so a 50-user arm A came out as 25 readers
# and 25 writers that each hit StopUser immediately. That halved the read load
# and made arm A look 20 ms faster than it was. Removing the class from the
# module is what actually keeps it out of the run.
if WRITE_INTERVAL_S <= 0:
    del CacheInvalidator
