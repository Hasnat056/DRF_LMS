"""
test_permissions_extended.py
-----------------------------
Object-level permission branch tests not covered by test_permissions.py.
"""
from datetime import date

import pytest
from django.contrib.auth.models import User, Group
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from Models.models import (
    Person, Student, Faculty, Enrollment, CourseAllocation, Reviews,
)

STUDENT = '/api/student'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(user):
    client = APIClient()
    token = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def _make_superuser():
    user = User.objects.create_superuser(
        username='super@test.com', password='superpass', email='super@test.com',
    )
    return user


# ---------------------------------------------------------------------------
# ReviewPermission — object-level branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReviewPermissionObjectLevel:

    def test_admin_has_object_permission_returns_false(
        self, admin_client, student_instance, active_enrollment, review
    ):
        """Admin can list reviews (SAFE_METHODS at has_permission) but object
        permission returns False because admin is neither Student nor Faculty group."""
        sid = student_instance.student_id.person_id
        url = f'{STUDENT}/{sid}/enrollments/{active_enrollment.enrollment_id}/reviews/{review.review_id}/'
        r = admin_client.get(url)
        assert r.status_code == 403

    def test_superuser_delete_blocked_at_has_permission(
        self, student_instance, active_enrollment, review
    ):
        """Superuser with DELETE → has_permission returns False (line 23: not DELETE)."""
        su = _make_superuser()
        client = _make_client(su)
        sid = student_instance.student_id.person_id
        url = f'{STUDENT}/{sid}/enrollments/{active_enrollment.enrollment_id}/reviews/{review.review_id}/'
        r = client.delete(url)
        assert r.status_code == 403

    def test_superuser_get_blocked_at_object_permission(
        self, student_instance, active_enrollment, review
    ):
        """Superuser passes has_permission for GET but hits return False at
        has_object_permission (not in Student or Faculty group)."""
        su = _make_superuser()
        client = _make_client(su)
        sid = student_instance.student_id.person_id
        url = f'{STUDENT}/{sid}/enrollments/{active_enrollment.enrollment_id}/reviews/{review.review_id}/'
        r = client.get(url)
        assert r.status_code == 403

    def test_faculty_object_permission_own_allocation(
        self, faculty_client, student_instance, active_enrollment, review
    ):
        """Faculty who owns the allocation can access the review object."""
        sid = student_instance.student_id.person_id
        url = f'{STUDENT}/{sid}/enrollments/{active_enrollment.enrollment_id}/reviews/{review.review_id}/'
        r = faculty_client.get(url)
        assert r.status_code == 200

    def test_faculty_object_permission_other_allocation(
        self, student_instance, active_enrollment, review, department
    ):
        """Faculty who does NOT own the allocation is blocked at object level."""
        other_user = User.objects.create_user(
            username='other_faculty@test.com', password='pass',
        )
        other_person = Person.objects.create(
            person_id='OTHER-FAC-001', first_name='Other', last_name='Faculty',
            father_name='Father', gender='Male', dob=date(1990, 1, 1),
            cnic='88888-8888888-8', contact_number='+923008888888',
            institutional_email='other_faculty@test.com', type='Faculty',
            user=other_user,
        )
        grp, _ = Group.objects.get_or_create(name='Faculty')
        other_user.groups.add(grp)
        Faculty.objects.create(
            employee_id=other_person, department=department,
            designation='Lecturer', joining_date=date(2021, 1, 1),
        )
        client = _make_client(other_user)
        sid = student_instance.student_id.person_id
        url = f'{STUDENT}/{sid}/enrollments/{active_enrollment.enrollment_id}/reviews/{review.review_id}/'
        r = client.get(url)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# StudentEnrollmentPermission — object-level
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentEnrollmentPermissionObjectLevel:

    def test_student_owns_enrollment_object_perm(
        self, student_client, active_enrollment
    ):
        """Student can retrieve own enrollment (object perm passes)."""
        from django.urls import reverse
        # Set semester to Active so it appears in list
        semester = active_enrollment.allocation.semester
        semester.status = 'Active'
        semester.save()
        url = reverse('Student:enrollment-detail', kwargs={
            'enrollment_id': active_enrollment.enrollment_id,
        })
        r = student_client.get(url)
        assert r.status_code == 200

    def test_other_student_blocked_at_object_level(
        self, active_enrollment, student_instance
    ):
        """Another student cannot retrieve first student's enrollment.
        Queryset filters by user → returns 404 (not 403)."""
        other_user = User.objects.create_user(
            username='other_student@test.com', password='pass',
        )
        grp, _ = Group.objects.get_or_create(name='Student')
        other_user.groups.add(grp)
        other_person = Person.objects.create(
            person_id='OTHER-STU-001', first_name='Other', last_name='Student',
            father_name='Father', gender='Male', dob=date(2001, 1, 1),
            cnic='77777-7777777-7', contact_number='+923007777777',
            institutional_email='other_student@test.com', type='Student',
            user=other_user,
        )
        Student.objects.create(
            student_id=other_person,
            program=student_instance.program,
            student_class=student_instance.student_class,
            admission_date=date(2023, 1, 1),
            status='Active',
        )
        client = _make_client(other_user)
        from django.urls import reverse
        url = reverse('Student:enrollment-detail', kwargs={
            'enrollment_id': active_enrollment.enrollment_id,
        })
        r = client.get(url)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# StudentAssessmentUploadPermission — object-level
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentAssessmentUploadPermissionObjectLevel:

    def test_other_student_cannot_patch_upload(
        self, submission_assessment_checked, active_enrollment,
        submission_assessment, student_instance
    ):
        """Another student cannot PATCH the first student's assessment upload."""
        other_user = User.objects.create_user(
            username='other_stu2@test.com', password='pass',
        )
        grp, _ = Group.objects.get_or_create(name='Student')
        other_user.groups.add(grp)
        other_person = Person.objects.create(
            person_id='OTHER-STU-002', first_name='Other2', last_name='Student',
            father_name='Father', gender='Male', dob=date(2001, 2, 1),
            cnic='66666-6666666-6', contact_number='+923006666666',
            institutional_email='other_stu2@test.com', type='Student',
            user=other_user,
        )
        Student.objects.create(
            student_id=other_person,
            program=student_instance.program,
            student_class=student_instance.student_class,
            admission_date=date(2023, 1, 1),
            status='Active',
        )
        client = _make_client(other_user)
        from django.urls import reverse
        url = reverse('Student:assessment-upload', kwargs={
            'enrollment_id': active_enrollment.enrollment_id,
            'assessment_id': submission_assessment.assessment_id,
            'id': submission_assessment_checked.id,
        })
        r = client.patch(url, {}, format='json')
        assert r.status_code == 403

    def test_student_can_patch_own_upload(
        self, student_client, submission_assessment_checked,
        active_enrollment, submission_assessment
    ):
        """Student who owns the enrollment can PATCH (permission passes)."""
        from django.urls import reverse
        url = reverse('Student:assessment-upload', kwargs={
            'enrollment_id': active_enrollment.enrollment_id,
            'assessment_id': submission_assessment.assessment_id,
            'id': submission_assessment_checked.id,
        })
        r = student_client.patch(url, {}, format='json')
        # 200 (no data changed) or 400 (validation) — but NOT 403
        assert r.status_code in (200, 400)
