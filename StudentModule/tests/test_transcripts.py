"""
test_transcripts.py
--------------------
Tests for StudentTranscriptListView — read-only, per-semester transcripts
scoped to the requesting student.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from Models.models import Transcript

STUDENT = '/api/student'


@pytest.fixture
def transcript(db, student_instance, inactive_semester):
    return Transcript.objects.create(
        student=student_instance,
        semester=inactive_semester,
        total_credits=15,
        semester_gpa=Decimal('3.25'),
    )


@pytest.mark.django_db
class TestTranscriptList:

    def test_list_own_transcripts(self, student_client, transcript):
        r = student_client.get(f'{STUDENT}/transcripts/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [t['id'] for t in results]
        assert transcript.id in ids

    def test_transcript_fields(self, student_client, transcript):
        r = student_client.get(f'{STUDENT}/transcripts/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        entry = next(t for t in results if t['id'] == transcript.id)
        assert entry['semester'] == transcript.semester_id
        assert entry['total_credits'] == 15
        assert entry['semester_gpa'] == '3.25'

    def test_filter_by_semester(self, student_client, transcript):
        r = student_client.get(f'{STUDENT}/transcripts/?semester={transcript.semester_id}')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        assert all(t['semester'] == transcript.semester_id for t in results)

    def test_empty_list_when_no_transcripts(self, student_client, student_instance):
        r = student_client.get(f'{STUDENT}/transcripts/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        assert results == []

    def test_student_cannot_see_other_students_transcripts(
        self, student_client, transcript, student_instance
    ):
        from django.contrib.auth.models import User, Group
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        from Models.models import Person, Student
        from datetime import date

        u2 = User.objects.create_user(username='other-transcript@test.com', password='pass')
        Group.objects.get_or_create(name='Student')
        u2.groups.add(Group.objects.get(name='Student'))
        p2 = Person.objects.create(
            person_id='OTHER-TR-001', first_name='Other', last_name='Student',
            father_name='F', gender='Male', dob=date(2001, 1, 1),
            cnic='99999-8888888-9', contact_number='+923009999998',
            institutional_email='other-transcript@test.com', type='Student', user=u2,
        )
        Student.objects.create(
            student_id=p2,
            program=student_instance.program,
            student_class=student_instance.student_class,
            admission_date=date(2023, 1, 1),
            status='Active',
        )
        token = str(RefreshToken.for_user(u2).access_token)
        client2 = APIClient()
        client2.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        r = client2.get(f'{STUDENT}/transcripts/')
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [t['id'] for t in results]
        assert transcript.id not in ids

    def test_unauthenticated_request_rejected(self, transcript):
        from rest_framework.test import APIClient
        r = APIClient().get(f'{STUDENT}/transcripts/')
        assert r.status_code == 401

    def test_write_methods_not_allowed(self, student_client, transcript):
        """StudentTranscriptPermission only allows SAFE_METHODS, so POST is
        rejected at the permission layer (403) before reaching dispatch."""
        r = student_client.post(f'{STUDENT}/transcripts/', data={})
        assert r.status_code == 403
