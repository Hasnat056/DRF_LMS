"""
StudentModule-specific fixtures that extend the root conftest.py graph.
"""
import io
import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from Models.models import (
    Assessment, AssessmentChecked, Enrollment, Result,
    Lecture, Attendance, Reviews, CourseAllocation,
)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Active allocation / enrollment for student tests
# ---------------------------------------------------------------------------

@pytest.fixture
def active_allocation(db, course_allocation):
    """course_allocation with status='Active' so student queries include it."""
    course_allocation.status = 'Active'
    course_allocation.save()
    return course_allocation


@pytest.fixture
def active_enrollment(db, enrollment, active_allocation):
    """Enrollment linked to active_allocation, status='Active'."""
    enrollment.allocation = active_allocation
    enrollment.status = 'Active'
    enrollment.save()
    return enrollment


# ---------------------------------------------------------------------------
# Assessment with student_submission enabled
# ---------------------------------------------------------------------------

@pytest.fixture
def submission_assessment(db, active_allocation):
    return Assessment.objects.create(
        allocation=active_allocation,
        assessment_type='Assignment',
        assessment_name='Assignment 1',
        assessment_date=date.today(),
        weightage=20,
        total_marks=50,
        student_submission=True,
        submission_deadline=timezone.now() + timedelta(days=7),
    )


@pytest.fixture
def submission_assessment_checked(db, submission_assessment, active_enrollment):
    return AssessmentChecked.objects.create(
        assessment=submission_assessment,
        enrollment=active_enrollment,
        obtained=None,
    )


# ---------------------------------------------------------------------------
# Assessment with student_submission disabled
# ---------------------------------------------------------------------------

@pytest.fixture
def no_submission_assessment(db, active_allocation):
    return Assessment.objects.create(
        allocation=active_allocation,
        assessment_type='Quiz',
        assessment_name='Quiz 1',
        assessment_date=date.today(),
        weightage=10,
        total_marks=20,
        student_submission=False,
    )


@pytest.fixture
def no_submission_assessment_checked(db, no_submission_assessment, active_enrollment):
    return AssessmentChecked.objects.create(
        assessment=no_submission_assessment,
        enrollment=active_enrollment,
        obtained=None,
    )


# ---------------------------------------------------------------------------
# Lecture + Attendance
# ---------------------------------------------------------------------------

@pytest.fixture
def active_lecture(db, active_allocation, active_enrollment):
    lec = Lecture.objects.create(
        lecture_no=1,
        allocation=active_allocation,
        starting_time=timezone.now() - timedelta(hours=2),
        venue='Room 101',
        duration=60,
        topic='Intro',
    )
    Attendance.objects.create(
        lecture=lec,
        enrollment=active_enrollment,
        attendance_date=lec.starting_time.date(),
        is_present=True,
    )
    return lec


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

@pytest.fixture
def review(db, active_enrollment):
    return Reviews.objects.create(
        enrollment=active_enrollment,
        review_text='Great course!',
        rating=5,
    )


# ---------------------------------------------------------------------------
# Cache priming helpers (StudentEnrollmentCreate requires cache key)
# ---------------------------------------------------------------------------

@pytest.fixture
def primed_enrollment_cache(db, active_allocation, student_instance):
    """
    StudentEnrollmentCreatePermission now does a live DB check requiring
    active_allocation.semester.session.status == 'Available'. Also primes
    the display cache that .get()/.post() still read from.
    """
    session = active_allocation.semester.session
    session.status = 'Available'
    session.save()

    cache_key = f'enrollments:{student_instance.student_class.class_id}:semester:allocations'
    data = [{'allocation_id': active_allocation.allocation_id, 'confirm': False}]
    cache.set(cache_key, data, timeout=None)
    return data


# ---------------------------------------------------------------------------
# Mock for compiler HTTP calls
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_python_compiler():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'stdout': 'Hello, World!\n', 'stderr': ''}
    with patch('Compilers.serializers.requests.post', return_value=mock_resp) as m:
        yield m


@pytest.fixture
def mock_c_compiler():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'stdout': '42\n', 'stderr': ''}
    with patch('Compilers.serializers.requests.post', return_value=mock_resp) as m:
        yield m


@pytest.fixture
def mock_compiler_connection_error():
    import requests as req
    with patch('Compilers.serializers.requests.post', side_effect=req.exceptions.ConnectionError('refused')) as m:
        yield m


# ---------------------------------------------------------------------------
# Dummy uploaded files
# ---------------------------------------------------------------------------

@pytest.fixture
def py_file():
    content = b'print("Hello, World!")\n'
    return SimpleUploadedFile('main.py', content, content_type='text/plain')


@pytest.fixture
def c_file():
    content = b'#include<stdio.h>\nint main(){printf("42\\n");return 0;}\n'
    return SimpleUploadedFile('main.c', content, content_type='text/plain')


@pytest.fixture
def invalid_file():
    return SimpleUploadedFile('main.java', b'public class Main{}', content_type='text/plain')


@pytest.fixture
def zip_with_main_py():
    """In-memory zip containing main.py."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('main.py', 'print("zipped!")\n')
    buf.seek(0)
    return SimpleUploadedFile('bundle.zip', buf.read(), content_type='application/zip')


@pytest.fixture
def zip_without_main():
    """In-memory zip with no main.* file."""
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('helper.py', 'x = 1\n')
    buf.seek(0)
    return SimpleUploadedFile('bundle.zip', buf.read(), content_type='application/zip')
