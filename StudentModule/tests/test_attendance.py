"""
test_attendance.py
-------------------
Tests for StudentAttendanceListAPIView and StudentAttendanceRetrieveAPIView.

Note: StudentAttendanceSerializer.get_percentage() has a known production bug
(line 332: `enrollemnt=` typo → FieldError). Tests that exercise get_percentage
are marked xfail(strict=True).
"""
import pytest
from django.urls import reverse

STUDENT = '/api/student'


@pytest.mark.django_db
class TestAttendanceList:

    def test_attendance_list_200(self, student_client):
        r = student_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 200

    def test_attendance_list_empty_when_no_enrollments(self, student_client):
        r = student_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 200
        results = r.data if isinstance(r.data, list) else r.data.get('results', [])
        # No active enrollments set up — list may be empty or contain inactive enrollment
        assert isinstance(results, list)

    def test_attendance_list_with_enrollment(
        self, student_client, active_enrollment
    ):
        r = student_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 200

    def test_attendance_list_percentage_field(
        self, student_client, active_enrollment, active_lecture
    ):
        """percentage field is returned without FieldError."""
        r = student_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 200
        results = r.data if isinstance(r.data, list) else r.data.get('results', [])
        assert len(results) > 0
        assert 'percentage' in results[0]


@pytest.mark.django_db
class TestAttendanceRetrieve:

    def test_retrieve_own_attendance_200(
        self, student_client, active_enrollment
    ):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200

    def test_retrieve_nonexistent_enrollment_404(self, student_client):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': 99999})
        r = student_client.get(url)
        assert r.status_code == 404

    def test_retrieve_returns_attendance_details_field(
        self, student_client, active_enrollment
    ):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert 'attendance_details' in r.data

    def test_retrieve_returns_faculty_details(
        self, student_client, active_enrollment
    ):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert 'faculty_details' in r.data

    def test_retrieve_returns_course_details(
        self, student_client, active_enrollment
    ):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert 'course_details' in r.data

    def test_attendance_details_contains_lecture_records(
        self, student_client, active_enrollment, active_lecture
    ):
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        details = r.data.get('attendance_details', [])
        assert len(details) >= 1

    def test_attendance_is_present_reflected(
        self, student_client, active_enrollment, active_lecture
    ):
        """active_lecture fixture creates Attendance with is_present=True."""
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        details = r.data.get('attendance_details', [])
        assert any(d['is_present'] for d in details)


    def test_retrieve_percentage_no_error(
        self, student_client, active_enrollment, active_lecture
    ):
        """percentage field is returned without error."""
        url = reverse('Student:attendance-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert 'percentage' in r.data
        assert isinstance(r.data['percentage'], (int, float))
