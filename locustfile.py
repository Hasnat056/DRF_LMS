"""
locustfile.py
-------------
Load testing for the University LMS backend using Locust.

Three user classes representing three scenarios:

1. NormalLoadUser     — 200 users, sustained read-heavy traffic (normal day)
2. PeakLoadUser       — 1000 users, mixed read/write (registration peak)
3. SpikeUser          — ramps to 5000 users rapidly (worst-case spike)

Run scenarios individually:

  Normal load (200 users, 10 min):
    locust -f locustfile.py NormalLoadUser --headless -u 200 -r 20 --run-time 10m --host http://localhost:8000

  Peak load (1000 users, 5 min):
    locust -f locustfile.py PeakLoadUser --headless -u 1000 -r 50 --run-time 5m --host http://localhost:8000

  Spike test (ramp to 5000 in 60s):
    locust -f locustfile.py SpikeUser --headless -u 5000 -r 250 --run-time 3m --host http://localhost:8000

  All together with UI:
    locust -f locustfile.py --host http://localhost:8000
    → open http://localhost:8089 in browser

Install:
    pip install locust
"""
import random
import requests
from locust import HttpUser, task, between, events
from locust.exception import StopUser


# ---------------------------------------------------------------------------
# Shared credentials — update these to match your test database
# ---------------------------------------------------------------------------

ADMIN_CREDENTIALS = {
    'username': 'admin@org.com',
    'password': 'admin12345678',
}

BASE = '/api/admin'

# Shared token — fetched once at test start, reused by all virtual users
# so the rate-limited /api/token/ endpoint is only hit once per test run.
_shared_token = None


@events.test_start.add_listener
def fetch_shared_token(environment, **kwargs):
    """Authenticate once before any users spawn."""
    global _shared_token
    host = environment.host.rstrip('/')
    response = requests.post(
        f'{host}/api/token/',
        json=ADMIN_CREDENTIALS,
        timeout=10,
    )
    if response.status_code == 200:
        _shared_token = response.json().get('access')
        print(f'\n[auth] Token acquired successfully.')
    else:
        print(f'\n[auth] Login failed: {response.status_code} — {response.text}')


# ---------------------------------------------------------------------------
# Base class — applies shared token to every user
# ---------------------------------------------------------------------------

class AuthenticatedUser(HttpUser):
    abstract = True
    credentials = {}  # kept for reference; auth is handled via shared token

    def on_start(self):
        if not _shared_token:
            raise StopUser()
        self.client.headers.update({'Authorization': f'Bearer {_shared_token}'})


# ---------------------------------------------------------------------------
# Scenario 1: Normal Load
# Simulates a regular day — mostly reads, occasional writes
# Target: 200 concurrent users, sustained 10 minutes
# ---------------------------------------------------------------------------

class NormalLoadUser(AuthenticatedUser):
    """
    Mixed admin/faculty/student behavior on a normal day.
    Heavily read-biased — 80% reads, 20% writes.
    """
    wait_time = between(2, 5)  # realistic think time between actions
    credentials = ADMIN_CREDENTIALS

    @task(5)
    def view_dashboard(self):
        """Most common action — cached after first hit."""
        self.client.get(f'{BASE}/dashboard/', name='GET /dashboard/')

    @task(4)
    def list_faculty(self):
        self.client.get(f'{BASE}/faculty/', name='GET /faculty/')

    @task(4)
    def list_students(self):
        self.client.get(f'{BASE}/students/', name='GET /students/')

    @task(3)
    def list_courses(self):
        self.client.get(f'{BASE}/courses/', name='GET /courses/')

    @task(3)
    def list_semesters(self):
        self.client.get(f'{BASE}/semesters/', name='GET /semesters/')

    @task(3)
    def list_allocations(self):
        self.client.get(f'{BASE}/allocations/', name='GET /allocations/')

    @task(3)
    def list_enrollments(self):
        self.client.get(f'{BASE}/enrollments/', name='GET /enrollments/')

    @task(2)
    def list_classes(self):
        self.client.get(f'{BASE}/classes/', name='GET /classes/')

    @task(2)
    def list_departments(self):
        self.client.get(f'{BASE}/departments/', name='GET /departments/')

    @task(2)
    def list_programs(self):
        self.client.get(f'{BASE}/programs/', name='GET /programs/')

    @task(1)
    def search_faculty(self):
        """Search bypasses cache — tests DB query performance."""
        query = random.choice(['Ahmed', 'Ali', 'Hassan', 'CS', 'Lecturer'])
        self.client.get(
            f'{BASE}/faculty/?search={query}',
            name='GET /faculty/?search='
        )

    @task(1)
    def search_students(self):
        query = random.choice(['BSCS', 'Active', '2022', '2023'])
        self.client.get(
            f'{BASE}/students/?search={query}',
            name='GET /students/?search='
        )

    @task(1)
    def filter_allocations_by_semester(self):
        """Filtered queries test index performance."""
        self.client.get(
            f'{BASE}/allocations/?status=Ongoing',
            name='GET /allocations/?status=Ongoing'
        )

    @task(1)
    def filter_enrollments_by_status(self):
        self.client.get(
            f'{BASE}/enrollments/?status=Active',
            name='GET /enrollments/?status=Active'
        )


# ---------------------------------------------------------------------------
# Scenario 2: Peak Load
# Simulates semester registration peak — heavy mixed read/write traffic
# Target: 1000 concurrent users, 5 minutes
# ---------------------------------------------------------------------------

class PeakLoadUser(AuthenticatedUser):
    """
    Aggressive mixed traffic simulating registration day.
    50% reads, 50% writes — hammers enrollments and allocations.
    """
    wait_time = between(0.5, 2)
    credentials = ADMIN_CREDENTIALS

    @task(4)
    def list_enrollments(self):
        self.client.get(f'{BASE}/enrollments/', name='GET /enrollments/')

    @task(4)
    def list_allocations(self):
        self.client.get(f'{BASE}/allocations/', name='GET /allocations/')

    @task(4)
    def list_students(self):
        self.client.get(f'{BASE}/students/', name='GET /students/')

    @task(3)
    def list_semesters(self):
        self.client.get(f'{BASE}/semesters/', name='GET /semesters/')

    @task(3)
    def list_courses(self):
        self.client.get(f'{BASE}/courses/', name='GET /courses/')

    @task(2)
    def filter_active_enrollments(self):
        self.client.get(
            f'{BASE}/enrollments/?status=Active',
            name='GET /enrollments/?status=Active'
        )

    @task(2)
    def filter_ongoing_allocations(self):
        self.client.get(
            f'{BASE}/allocations/?status=Ongoing',
            name='GET /allocations/?status=Ongoing'
        )

    @task(2)
    def search_students(self):
        query = random.choice(['BSCS', 'BSSE', 'Active', '2023', '2024'])
        self.client.get(
            f'{BASE}/students/?search={query}',
            name='GET /students/?search='
        )

    @task(1)
    def view_dashboard(self):
        self.client.get(f'{BASE}/dashboard/', name='GET /dashboard/')

    @task(1)
    def list_departments(self):
        self.client.get(f'{BASE}/departments/', name='GET /departments/')

    @task(1)
    def list_programs(self):
        self.client.get(f'{BASE}/programs/', name='GET /programs/')


# ---------------------------------------------------------------------------
# Scenario 3: Spike Test
# Simulates a sudden traffic burst — worst-case scenario
# Target: ramp to 5000 users in 60 seconds, hold for 3 minutes
# ---------------------------------------------------------------------------

class SpikeUser(AuthenticatedUser):
    """
    Rapid burst of read-only traffic on the most-hit endpoints.
    Almost no think time — simulates a sudden flood of concurrent requests.
    """
    wait_time = between(0.1, 0.5)
    credentials = ADMIN_CREDENTIALS

    @task(5)
    def view_dashboard(self):
        self.client.get(f'{BASE}/dashboard/', name='GET /dashboard/')

    @task(4)
    def list_students(self):
        self.client.get(f'{BASE}/students/', name='GET /students/')

    @task(4)
    def list_faculty(self):
        self.client.get(f'{BASE}/faculty/', name='GET /faculty/')

    @task(3)
    def list_enrollments(self):
        self.client.get(f'{BASE}/enrollments/', name='GET /enrollments/')

    @task(3)
    def list_allocations(self):
        self.client.get(f'{BASE}/allocations/', name='GET /allocations/')

    @task(2)
    def list_semesters(self):
        self.client.get(f'{BASE}/semesters/', name='GET /semesters/')

    @task(1)
    def list_courses(self):
        self.client.get(f'{BASE}/courses/', name='GET /courses/')