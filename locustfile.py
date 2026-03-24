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
import json
from locust import HttpUser, task, between, events
from locust.exception import StopUser


# ---------------------------------------------------------------------------
# Shared credentials — update these to match your test database
# ---------------------------------------------------------------------------

ADMIN_CREDENTIALS = {
    'username': 'rhays056@gmail.com',
    'password': 'admin12345678',
}

BASE = '/api/admin'


# ---------------------------------------------------------------------------
# Base class — handles authentication
# ---------------------------------------------------------------------------

class AuthenticatedUser(HttpUser):
    abstract = True
    token = None

    def on_start(self):
        """Authenticate and store JWT token before running tasks."""
        self.token = self._login(self.credentials)
        if not self.token:
            raise StopUser()
        self.client.headers.update({'Authorization': f'Bearer {self.token}'})

    def _login(self, credentials):
        with self.client.post(
            '/api/token/',
            json=credentials,
            catch_response=True,
            name='POST /api/token/ [auth]'
        ) as response:
            if response.status_code == 200:
                return response.json().get('access')
            else:
                response.failure(f'Login failed: {response.status_code}')
                return None


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