"""
test_bulk_allocations.py
-------------------------
Tests for BulkCourseAllocationAPIView — the allocation worksheet.

GET  builds the per-class worksheet for the live session, in any live phase.
POST is open only while the session is Initiated — that is the setup window.
Once it goes Available, enrollment references these allocations, so the
worksheet turns read-only and a single correction goes through
allocations/<id>/ instead.
"""
import pytest
from django.urls import reverse

from Models.models import (
    AcademicSession, Course, CourseAllocation, Semester, SemesterDetails,
)

BULK = '/api/admin/allocations/bulk/'


@pytest.fixture
def second_course(db):
    return Course.objects.create(
        course_code='CS-102', course_name='Discrete Maths', credit_hours=3, lab=False,
    )


@pytest.fixture
def worksheet(db, inactive_semester, course, second_course):
    """inactive_semester already has `course` in its scheme of studies;
    add a second so we can test partial allocation."""
    SemesterDetails.objects.create(semester=inactive_semester, course=second_course)
    return inactive_semester


# ---------------------------------------------------------------------------
# GET — the worksheet
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkAllocationWorksheet:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(BULK).status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(BULK).status_code == 403

    def test_returns_empty_when_no_live_session(self, admin_client, batch_class):
        AcademicSession.objects.all().update(status='Completed')
        r = admin_client.get(BULK)
        assert r.status_code == 200
        assert r.data == {'session': None, 'classes': []}

    def test_lists_scheme_of_studies_courses(self, admin_client, worksheet):
        r = admin_client.get(BULK)
        assert r.status_code == 200
        assert r.data['session']['status'] == 'Initiated'
        entry = next(c for c in r.data['classes'] if c['semester_id'] == worksheet.semester_id)
        codes = {c['course_code'] for c in entry['courses']}
        assert codes == {'CS-101', 'CS-102'}

    def test_unallocated_course_has_null_allocation(self, admin_client, worksheet):
        r = admin_client.get(BULK)
        entry = next(c for c in r.data['classes'] if c['semester_id'] == worksheet.semester_id)
        for row in entry['courses']:
            assert row['allocation_id'] is None
            assert row['faculty'] is None

    def test_allocated_course_reports_its_faculty(
        self, admin_client, worksheet, faculty_instance, course
    ):
        allocation = CourseAllocation.objects.create(
            semester=worksheet, course=course, faculty=faculty_instance,
            session=str(worksheet.session), status='Inactive',
        )
        r = admin_client.get(BULK)
        entry = next(c for c in r.data['classes'] if c['semester_id'] == worksheet.semester_id)
        allocated = next(c for c in entry['courses'] if c['course_code'] == 'CS-101')
        pending = next(c for c in entry['courses'] if c['course_code'] == 'CS-102')

        assert allocated['allocation_id'] == allocation.allocation_id
        assert allocated['faculty']['employee_id'] == faculty_instance.employee_id.person_id
        assert pending['allocation_id'] is None

    def test_placeholder_semesterdetails_rows_are_skipped(
        self, admin_client, worksheet
    ):
        """Class creation seeds a SemesterDetails row with course=None."""
        SemesterDetails.objects.create(semester=worksheet, course=None)
        r = admin_client.get(BULK)
        entry = next(c for c in r.data['classes'] if c['semester_id'] == worksheet.semester_id)
        assert all(c['course_code'] is not None for c in entry['courses'])
        assert len(entry['courses']) == 2


# ---------------------------------------------------------------------------
# POST — Initiated phase (upsert)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkAllocationCreate:

    def _payload(self, semester, course, faculty):
        return [{'semester': semester.semester_id,
                 'course': course.course_code,
                 'faculty': faculty.pk}]

    def test_creates_allocations(self, admin_client, worksheet, course, faculty_instance):
        r = admin_client.post(
            BULK, self._payload(worksheet, course, faculty_instance), format='json'
        )
        assert r.status_code == 201
        assert r.data == {'created': 1, 'updated': 0}
        assert CourseAllocation.objects.filter(semester=worksheet, course=course).exists()

    def test_derives_session_and_status(self, admin_client, worksheet, course, faculty_instance):
        admin_client.post(BULK, self._payload(worksheet, course, faculty_instance), format='json')
        allocation = CourseAllocation.objects.get(semester=worksheet, course=course)
        assert allocation.session == str(worksheet.session)
        assert allocation.status == 'Inactive'

    def test_resubmitting_unchanged_is_a_noop(
        self, admin_client, worksheet, course, faculty_instance
    ):
        payload = self._payload(worksheet, course, faculty_instance)
        admin_client.post(BULK, payload, format='json')
        r = admin_client.post(BULK, payload, format='json')
        assert r.status_code == 201
        assert r.data == {'created': 0, 'updated': 0}
        assert CourseAllocation.objects.filter(semester=worksheet, course=course).count() == 1

    def test_changed_faculty_updates_in_place(
        self, admin_client, worksheet, course, faculty_instance, second_faculty
    ):
        admin_client.post(BULK, self._payload(worksheet, course, faculty_instance), format='json')
        original = CourseAllocation.objects.get(semester=worksheet, course=course)

        r = admin_client.post(
            BULK, self._payload(worksheet, course, second_faculty), format='json'
        )
        assert r.status_code == 201
        assert r.data == {'created': 0, 'updated': 1}

        original.refresh_from_db()
        assert original.faculty_id == second_faculty.pk
        # same row — not a delete + re-insert
        assert CourseAllocation.objects.filter(semester=worksheet, course=course).count() == 1

    def test_course_outside_scheme_of_studies_rejected(
        self, admin_client, worksheet, faculty_instance
    ):
        stray = Course.objects.create(
            course_code='ZZ-999', course_name='Unrelated', credit_hours=3, lab=False,
        )
        r = admin_client.post(
            BULK, self._payload(worksheet, stray, faculty_instance), format='json'
        )
        assert r.status_code == 400
        assert not CourseAllocation.objects.filter(course=stray).exists()

    def test_duplicate_rows_rejected(self, admin_client, worksheet, course, faculty_instance):
        row = self._payload(worksheet, course, faculty_instance)[0]
        r = admin_client.post(BULK, [row, row], format='json')
        assert r.status_code == 400
        assert not CourseAllocation.objects.filter(semester=worksheet).exists()

    def test_empty_payload_rejected(self, admin_client):
        assert admin_client.post(BULK, [], format='json').status_code == 400

    def test_nothing_written_when_one_row_is_invalid(
        self, admin_client, worksheet, course, second_course, faculty_instance
    ):
        """All-or-nothing — a bad row must not leave the good ones behind."""
        stray = Course.objects.create(
            course_code='ZZ-998', course_name='Unrelated', credit_hours=3, lab=False,
        )
        r = admin_client.post(BULK, [
            {'semester': worksheet.semester_id, 'course': course.course_code,
             'faculty': faculty_instance.pk},
            {'semester': worksheet.semester_id, 'course': stray.course_code,
             'faculty': faculty_instance.pk},
        ], format='json')
        assert r.status_code == 400
        assert CourseAllocation.objects.filter(semester=worksheet).count() == 0

    def test_active_semester_rejected(
        self, admin_client, active_semester, course, faculty_instance
    ):
        SemesterDetails.objects.get_or_create(semester=active_semester, course=course)
        r = admin_client.post(
            BULK, self._payload(active_semester, course, faculty_instance), format='json'
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST — closed outside the Initiated phase
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkAllocationWriteWindow:
    """Bulk writing is open only while the session is Initiated. Once it goes
    Available, enrollment references these allocations and the worksheet is
    read-only — a single correction goes through allocations/<id>/ instead."""

    def _payload(self, semester, course, faculty):
        return [{'semester': semester.semester_id,
                 'course': course.course_code,
                 'faculty': faculty.pk}]

    @pytest.mark.parametrize('closed_status', ['Inactive', 'Available', 'Active', 'Completed'])
    def test_post_rejected_outside_initiated(
        self, admin_client, worksheet, course, faculty_instance, closed_status
    ):
        worksheet.session.status = closed_status
        worksheet.session.save()

        r = admin_client.post(
            BULK, self._payload(worksheet, course, faculty_instance), format='json'
        )
        assert r.status_code == 400
        assert not CourseAllocation.objects.filter(semester=worksheet).exists()

    def test_get_still_works_when_available(self, admin_client, worksheet):
        """The worksheet stays readable after initiation."""
        worksheet.session.status = 'Available'
        worksheet.session.save()

        r = admin_client.get(BULK)
        assert r.status_code == 200
        assert r.data['session']['status'] == 'Available'
        assert any(c['semester_id'] == worksheet.semester_id for c in r.data['classes'])


# ---------------------------------------------------------------------------
# Single-allocation correction during Available
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSingleAllocationFacultyChange:

    @pytest.fixture
    def allocation(self, worksheet, course, faculty_instance):
        return CourseAllocation.objects.create(
            semester=worksheet, course=course, faculty=faculty_instance,
            session=str(worksheet.session), status='Inactive',
        )

    def _url(self, allocation):
        return reverse('Admin:allocation-detail',
                       kwargs={'allocation_id': allocation.allocation_id})

    def test_faculty_can_be_reassigned_when_available(
        self, admin_client, worksheet, allocation, second_faculty
    ):
        worksheet.session.status = 'Available'
        worksheet.session.save()

        r = admin_client.patch(
            self._url(allocation), {'faculty': second_faculty.pk}, format='json'
        )
        assert r.status_code == 200
        allocation.refresh_from_db()
        assert allocation.faculty_id == second_faculty.pk

    def test_course_cannot_be_changed(
        self, admin_client, worksheet, allocation, second_course
    ):
        """Course comes from the scheme of studies — read-only, so the PATCH
        is accepted but the field is ignored."""
        worksheet.session.status = 'Available'
        worksheet.session.save()
        original_course = allocation.course_id

        admin_client.patch(
            self._url(allocation), {'course': second_course.course_code}, format='json'
        )
        allocation.refresh_from_db()
        assert allocation.course_id == original_course

    def test_patch_blocked_while_initiated(
        self, admin_client, worksheet, allocation, second_faculty
    ):
        """During Initiated the worksheet owns allocation edits."""
        assert worksheet.session.status == 'Initiated'
        r = admin_client.patch(
            self._url(allocation), {'faculty': second_faculty.pk}, format='json'
        )
        assert r.status_code == 403


@pytest.mark.django_db
class TestWorksheetSkeletonCache:
    """Only the class/semester/course skeleton is cached — allocations are read
    live, so a POST never has to invalidate anything."""

    def _key(self, session):
        return f'admin:{session.id}:allocations:bulk'

    def test_skeleton_is_cached_on_first_read(self, admin_client, worksheet):
        from django.core.cache import cache
        cache.delete(self._key(worksheet.session))

        admin_client.get(BULK)

        cached = cache.get(self._key(worksheet.session))
        assert cached is not None
        assert any(e['semester_id'] == worksheet.semester_id for e in cached)

    def test_cached_skeleton_carries_no_allocation_data(self, admin_client, worksheet):
        """Allocations must stay out of the cache — that is what lets a POST
        leave it alone."""
        from django.core.cache import cache
        admin_client.get(BULK)

        cached = cache.get(self._key(worksheet.session))
        for entry in cached:
            for course in entry['courses']:
                assert 'allocation_id' not in course
                assert 'faculty' not in course

    def test_allocations_appear_without_clearing_the_cache(
        self, admin_client, worksheet, course, faculty_instance
    ):
        """The whole point of the split: allocate, and the change shows up on
        the next read even though the skeleton was never invalidated."""
        from django.core.cache import cache
        admin_client.get(BULK)                       # warm the skeleton
        before = cache.get(self._key(worksheet.session))

        r = admin_client.post(BULK, [{
            'semester': worksheet.semester_id,
            'course': course.course_code,
            'faculty': faculty_instance.pk,
        }], format='json')
        assert r.status_code == 201

        after = admin_client.get(BULK)
        entry = next(c for c in after.data['classes'] if c['semester_id'] == worksheet.semester_id)
        allocated = next(c for c in entry['courses'] if c['course_code'] == course.course_code)

        assert allocated['allocation_id'] is not None
        assert allocated['faculty'] is not None
        # untouched by the write
        assert cache.get(self._key(worksheet.session)) == before

    def test_scheme_of_studies_edit_invalidates(
        self, admin_client, worksheet, batch_class, academic_session
    ):
        """Courses come from the scheme of studies, so editing it makes the
        cached skeleton wrong."""
        from django.core.cache import cache
        admin_client.get(BULK)
        assert cache.get(self._key(worksheet.session)) is not None

        admin_client.patch(
            reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id}),
            {'scheme_of_studies': [
                {'semester_id': worksheet.semester_id,
                 'semesterdetails_set': [{'course': None}]},
            ]},
            format='json',
        )

        assert cache.get(self._key(worksheet.session)) is None

    def test_response_is_identical_cached_or_not(
        self, admin_client, worksheet, course, faculty_instance
    ):
        from django.core.cache import cache
        CourseAllocation.objects.create(
            semester=worksheet, course=course, faculty=faculty_instance,
            session=str(worksheet.session), status='Inactive',
        )
        cache.delete(self._key(worksheet.session))

        cold = admin_client.get(BULK).data
        warm = admin_client.get(BULK).data

        assert cold == warm
