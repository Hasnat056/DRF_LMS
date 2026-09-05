"""
Fixtures for the endpoint performance audit.

The stress dataset is seeded **once**, outside the per-test transaction, via
`django_db_blocker.unblock()`. Combined with `--reuse-db` that means the
multi-minute seed happens on the first run against a fresh test database and
never again. Individual benchmarks still run inside the usual rollback
transaction, which is harmless — they only read.
"""
import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from Models.models import (
    AcademicSession, CourseAllocation, Enrollment, Semester, Student,
)
from benchmarks import seed_stress


@pytest.fixture(scope='session')
def django_db_modify_db_settings(django_db_modify_db_settings):
    """Give the benchmarks a database of their own.

    `--reuse-db` keeps one test database between runs, and the stress dataset
    is not compatible with the normal suite: it seeds Fall-2024 and
    Spring-2025, which `conftest.py`'s `academic_session` fixtures then
    collide with on `unique(period, year)`. Seeding into the shared
    `test_LMS` took the suite from 774 passing to 370 errors.

    pytest-django applies this fixture before creating the database, and it is
    only requested by tests collected under `benchmarks/` — a normal run
    deselects those (`-m "not benchmark"`) and is untouched.
    """
    from django.conf import settings
    for alias in settings.DATABASES:
        settings.DATABASES[alias].setdefault('TEST', {})
        settings.DATABASES[alias]['TEST']['NAME'] = (
            f"test_{settings.DATABASES[alias]['NAME']}_bench"
        )


@pytest.fixture(scope='session')
def stress_data(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        seed_stress.seed(out=None)
    return True


def _client(username):
    user = User.objects.get(username=username)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}'
    )
    return client


@pytest.fixture
def bench_admin_client(db, stress_data):
    return _client(seed_stress.ADMIN_EMAIL)


@pytest.fixture
def bench_faculty_client(db, stress_data):
    return _client(seed_stress.FACULTY_EMAIL)


@pytest.fixture
def bench_student_client(db, stress_data):
    return _client(seed_stress.STUDENT_EMAIL)


@pytest.fixture
def bench_anon_client(db, stress_data):
    return APIClient()


@pytest.fixture
def bench_ids(db, stress_data):
    """Concrete primary keys from the seeded data, for detail-route URLs.

    Chosen from the *live* session wherever there is a choice — that is the
    hot path an admin or faculty member actually hits day to day.
    """
    live = AcademicSession.objects.get(
        period=seed_stress.MARKER_PERIOD, year=seed_stress.MARKER_YEAR
    )
    faculty_alloc = (
        CourseAllocation.objects
        .filter(semester__session=live,
                faculty__employee_id__institutional_email=seed_stress.FACULTY_EMAIL)
        .select_related('semester')
        .first()
    )
    student = Student.objects.get(
        student_id__institutional_email=seed_stress.STUDENT_EMAIL
    )
    student_enrollment = (
        Enrollment.objects
        .filter(student=student, allocation__semester__session=live)
        .first()
    )
    return {
        'session': live.id,
        'semester': faculty_alloc.semester_id,
        'allocation': faculty_alloc.allocation_id,
        'assessment': faculty_alloc.assessment_set.values_list(
            'assessment_id', flat=True
        ).first(),
        'lecture': faculty_alloc.lecture_set.values_list(
            'lecture_id', flat=True
        ).first(),
        'student_id': student.pk,
        'student_enrollment': student_enrollment.enrollment_id,
        'class': Semester.objects.get(pk=faculty_alloc.semester_id).associated_class_id,
        'department': 'D00',
        'program': 'P000',
        'course': faculty_alloc.course_id,
        'faculty_employee_id': (
            CourseAllocation.objects.filter(pk=faculty_alloc.pk)
            .values_list('faculty_id', flat=True).first()
        ),
    }


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Undo the root conftest's eager-Celery fixture, for benchmarks only.

    The root `conftest.py:344` sets `CELERY_TASK_ALWAYS_EAGER = True` autouse,
    which makes `cache_*_data_task.delay(...)` run **inline, inside the request
    being timed**. Five admin list views fire one on a cache miss
    (`AdminModule/views.py:282,385,522,601,700`), so every "cold" figure in the
    first audit run was request + full cache rebuild — work production does on
    a worker. The fingerprint was three different query-param branches of
    `admin/faculty-list` all reporting an identical 149 queries cold.

    Redefining the fixture name here shadows the parent's for this directory.
    Jobs go to `redis-test` db 2, and `celery-worker-test` consumes them
    (`docker compose --profile test up -d`).
    """
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CELERY_TASK_EAGER_PROPAGATES = False
