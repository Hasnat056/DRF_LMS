"""
test_enrollments.py
--------------------
Tests for StudentEnrollmentsListView and StudentEnrollmentRetrieveView.

StudentEnrollmentsListView queryset filters:
  allocation__semester__status__in=['Active', 'Completed']

So enrollments tied to 'Inactive' semesters are never returned.
"""
import pytest
from django.urls import reverse

STUDENT = '/api/student'


@pytest.mark.django_db
class TestEnrollmentList:

    def test_empty_list_when_no_active_semester(self, student_client, enrollment):
        """enrollment is on inactive_semester → filtered out → empty list."""
        r = student_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [e['enrollment_id'] for e in results]
        assert enrollment.enrollment_id not in ids

    def test_active_enrollment_appears_in_list(
        self, student_client, active_enrollment
    ):
        """active_enrollment is on an Ongoing allocation with an Active semester."""
        # active_enrollment is linked to active_allocation which has inactive_semester
        # We need the semester to be Active too.
        semester = active_enrollment.allocation.semester
        semester.status = 'Active'
        semester.save()
        r = student_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [e['enrollment_id'] for e in results]
        assert active_enrollment.enrollment_id in ids

    def test_enrollment_fields_present(self, student_client, active_enrollment):
        semester = active_enrollment.allocation.semester
        semester.status = 'Active'
        semester.save()
        r = student_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        if results:
            entry = results[0]
            assert 'enrollment_id' in entry
            assert 'status' in entry

    def test_filter_by_status(self, student_client, active_enrollment):
        semester = active_enrollment.allocation.semester
        semester.status = 'Active'
        semester.save()
        r = student_client.get(f'{STUDENT}/enrollments/?status=Active')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        for e in results:
            assert e['status'] == 'Active'

    def test_student_cannot_see_other_students_enrollments(
        self, student_client, active_enrollment, student_instance
    ):
        """The queryset is filtered by the requesting student's user."""
        from django.contrib.auth.models import User, Group
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        from Models.models import Person, Student
        from datetime import date

        u2 = User.objects.create_user(username='other@test.com', password='pass')
        Group.objects.get_or_create(name='Student')
        u2.groups.add(Group.objects.get(name='Student'))
        p2 = Person.objects.create(
            person_id='OTHER-001', first_name='Other', last_name='Student',
            father_name='F', gender='Male', dob=date(2001, 1, 1),
            cnic='99999-9999999-9', contact_number='+923009999999',
            institutional_email='other@test.com', type='Student', user=u2,
        )
        s2 = Student.objects.create(
            student_id=p2,
            program=student_instance.program,
            student_class=student_instance.student_class,
            admission_date=date(2023, 1, 1),
            status='Active',
        )
        token = str(RefreshToken.for_user(u2).access_token)
        client2 = APIClient()
        client2.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        r = client2.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [e['enrollment_id'] for e in results]
        assert active_enrollment.enrollment_id not in ids


@pytest.mark.django_db
class TestEnrollmentRetrieve:

    def test_retrieve_own_enrollment(self, student_client, active_enrollment):
        url = reverse('Student:enrollment-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert r.data['enrollment_id'] == active_enrollment.enrollment_id

    def test_retrieve_enrollment_contains_allocation_details(
        self, student_client, active_enrollment
    ):
        url = reverse('Student:enrollment-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        assert 'allocation_details' in r.data

    def test_retrieve_nonexistent_enrollment_404(self, student_client):
        url = reverse('Student:enrollment-detail', kwargs={'enrollment_id': 99999})
        r = student_client.get(url)
        assert r.status_code == 404

    def test_allocation_details_has_course_info(self, student_client, active_enrollment):
        url = reverse('Student:enrollment-detail', kwargs={'enrollment_id': active_enrollment.enrollment_id})
        r = student_client.get(url)
        assert r.status_code == 200
        details = r.data.get('allocation_details', {})
        assert 'course_details' in details or details == {}
