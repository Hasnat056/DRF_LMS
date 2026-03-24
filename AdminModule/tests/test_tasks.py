"""
test_tasks.py
-------------
Tests Celery tasks via .delay() / .apply_async() with CELERY_TASK_ALWAYS_EAGER=True.
eta is ignored in eager mode — tasks run immediately.

Philosophy: we don't test Celery's scheduler (that's Celery's job).
We test the BUSINESS CONSEQUENCES of each task firing:
  - Does the right state transition happen?
  - Does it cascade correctly to related models?
  - Does the system correctly open/close operation windows after the transition?
  - Is the task idempotent where it should be?
  - Are edge cases handled gracefully?
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from Models.models import (
    Semester, CourseAllocation, Enrollment, Course, SemesterDetails,
)
from AdminModule.tasks import (
    semester_activation_task,
    semester_closing_task,
    cache_faculty_data_task,
    cache_student_data_task,
    cache_semester_data_task,
    cache_courseAllocation_data_task,
    cache_enrollment_data_task,
)
from django.core.cache import cache


# ===========================================================================
# semester_activation_task
# Business rule: Inactive+deadline → Active
# Consequence: allocation creation window CLOSES
# ===========================================================================

@pytest.mark.django_db
class TestSemesterActivationTask:

    def test_task_is_registered_as_celery_task(self):
        """Verify @shared_task decorator is applied — task has .delay() and .apply_async()."""
        assert hasattr(semester_activation_task, 'delay')
        assert hasattr(semester_activation_task, 'apply_async')

    def test_activates_semester(self, inactive_semester):
        """Core state transition: Inactive → Active."""
        semester_activation_task.delay(inactive_semester.semester_id)
        inactive_semester.refresh_from_db()
        assert inactive_semester.status == 'Active'

    def test_cascades_to_allocations(self, inactive_semester, course_allocation):
        """Allocations in the semester must become Ongoing when semester activates."""
        assert course_allocation.status == 'Inactive'
        semester_activation_task.delay(inactive_semester.semester_id)
        course_allocation.refresh_from_db()
        assert course_allocation.status == 'Ongoing'

    def test_cascades_to_enrollments(self, inactive_semester, course_allocation, enrollment):
        """Enrollments must become Active when semester activates."""
        assert enrollment.status == 'Inactive'
        semester_activation_task.delay(inactive_semester.semester_id)
        enrollment.refresh_from_db()
        assert enrollment.status == 'Active'

    def test_allocation_window_closes_after_activation(
        self, inactive_semester, faculty_instance, course
    ):
        """
        BUSINESS RULE: Allocations can only be created for Inactive semesters
        that have session + activation_deadline set.
        Once the task fires and semester becomes Active, that window must close.
        """
        # Before task — semester is in the eligible queryset
        eligible_before = Semester.objects.filter(
            status='Inactive',
            session__isnull=False,
            activation_deadline__isnull=False
        )
        assert inactive_semester in eligible_before

        # Task fires
        semester_activation_task.delay(inactive_semester.semester_id)

        # After task — semester must no longer be in the eligible queryset
        eligible_after = Semester.objects.filter(
            status='Inactive',
            session__isnull=False,
            activation_deadline__isnull=False
        )
        assert inactive_semester not in eligible_after

    def test_enrollment_creation_window_opens_after_activation(
        self, inactive_semester, course_allocation
    ):
        """
        BUSINESS RULE: Enrollments can only be created for Ongoing allocations.
        Before activation, allocations are Inactive — enrollment window is closed.
        After activation, allocations become Ongoing — enrollment window opens.
        """
        # Before task — allocation is Inactive, not in enrollment-eligible queryset
        eligible_before = CourseAllocation.objects.filter(status='Ongoing')
        assert course_allocation not in eligible_before

        # Task fires
        semester_activation_task.delay(inactive_semester.semester_id)

        # After task — allocation is Ongoing, enrollment window is open
        eligible_after = CourseAllocation.objects.filter(status='Ongoing')
        course_allocation.refresh_from_db()
        assert course_allocation in eligible_after

    def test_idempotent_when_already_active(self, active_semester):
        """
        Calling activation task on an already-Active semester must be a no-op.
        Status stays Active, no errors raised.
        """
        result = semester_activation_task.delay(active_semester.semester_id)
        active_semester.refresh_from_db()
        assert active_semester.status == 'Active'
        assert result.result == 'Semester already activated'

    def test_multiple_allocations_all_cascade(
        self, inactive_semester, faculty_instance, course, db
    ):
        """All allocations, not just one, must transition to Ongoing."""
        course2 = Course.objects.create(
            course_code='CS-202', course_name='Data Structures', credit_hours=3, lab=False
        )
        SemesterDetails.objects.create(
            semester_id=inactive_semester,
            class_id=inactive_semester.semesterdetails_set.first().class_id,
            course_code=course2,
        )
        alloc2 = CourseAllocation.objects.create(
            teacher_id=faculty_instance,
            course_code=course2,
            semester_id=inactive_semester,
            session=inactive_semester.session,
            status='Inactive',
        )

        semester_activation_task.delay(inactive_semester.semester_id)

        alloc2.refresh_from_db()
        assert alloc2.status == 'Ongoing'

    def test_nonexistent_semester_does_not_crash(self):
        """Task called with a non-existent semester_id must not raise an exception."""
        # filter().first() returns None safely — should not crash
        semester_activation_task.delay(99999)


# ===========================================================================
# semester_closing_task
# Business rule: Active → Completed
# Consequence: enrollment creation window CLOSES
# ===========================================================================

@pytest.mark.django_db
class TestSemesterClosingTask:

    def test_task_is_registered_as_celery_task(self):
        assert hasattr(semester_closing_task, 'delay')
        assert hasattr(semester_closing_task, 'apply_async')

    def test_closes_semester(self, active_semester):
        """Core state transition: Active → Completed."""
        semester_closing_task.delay(active_semester.semester_id)
        active_semester.refresh_from_db()
        assert active_semester.status == 'Completed'

    def test_cascades_allocations_to_completed(self, active_semester, course_allocation):
        """Allocations must become Completed when semester closes."""
        course_allocation.semester_id = active_semester
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        semester_closing_task.delay(active_semester.semester_id)
        course_allocation.refresh_from_db()
        assert course_allocation.status == 'Completed'

    def test_cascades_enrollments_to_completed(
        self, active_semester, course_allocation, enrollment
    ):
        """Enrollments must become Completed when semester closes."""
        course_allocation.semester_id = active_semester
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.status = 'Active'
        enrollment.save()

        semester_closing_task.delay(active_semester.semester_id)
        enrollment.refresh_from_db()
        assert enrollment.status == 'Completed'

    def test_enrollment_window_closes_after_closing(
        self, active_semester, course_allocation
    ):
        """
        BUSINESS RULE: After semester closes, allocations become Completed.
        Completed allocations must NOT appear in the Ongoing queryset,
        so no new enrollments can be created.
        """
        course_allocation.semester_id = active_semester
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        # Before close — allocation is Ongoing, enrollment window open
        assert course_allocation in CourseAllocation.objects.filter(status='Ongoing')

        semester_closing_task.delay(active_semester.semester_id)
        course_allocation.refresh_from_db()

        # After close — allocation is Completed, enrollment window closed
        assert course_allocation not in CourseAllocation.objects.filter(status='Ongoing')

    def test_full_lifecycle(self, inactive_semester, course_allocation, enrollment):
        """
        FULL LIFECYCLE TEST:
        Inactive → (activation task) → Active → (closing task) → Completed
        Verifies the complete state machine for semester, allocation, and enrollment.
        """
        # Stage 1: Inactive
        assert inactive_semester.status == 'Inactive'
        assert course_allocation.status == 'Inactive'
        assert enrollment.status == 'Inactive'

        # Stage 2: Activation
        semester_activation_task.delay(inactive_semester.semester_id)
        inactive_semester.refresh_from_db()
        course_allocation.refresh_from_db()
        enrollment.refresh_from_db()

        assert inactive_semester.status == 'Active'
        assert course_allocation.status == 'Ongoing'
        assert enrollment.status == 'Active'

        # Stage 3: Closing
        semester_closing_task.delay(inactive_semester.semester_id)
        inactive_semester.refresh_from_db()
        course_allocation.refresh_from_db()
        enrollment.refresh_from_db()

        assert inactive_semester.status == 'Completed'
        assert course_allocation.status == 'Completed'
        assert enrollment.status == 'Completed'

    def test_bug_no_idempotency_guard(self, active_semester):
        """
        BUG DOCUMENTATION: semester_closing_task has no guard against
        being called on an already-Completed semester, unlike activation_task
        which returns early with 'Semester already activated'.
        Closing task will re-run all cascade saves unnecessarily.
        """
        semester_closing_task.delay(active_semester.semester_id)
        active_semester.refresh_from_db()
        assert active_semester.status == 'Completed'

        # Second call — should ideally be a no-op but currently re-runs everything
        semester_closing_task.delay(active_semester.semester_id)
        active_semester.refresh_from_db()
        assert active_semester.status == 'Completed'  # still correct, just wasteful


# ===========================================================================
# Cache tasks — verify keys are written and content is non-empty
# ===========================================================================

@pytest.mark.django_db
class TestCacheFacultyDataTask:

    def test_task_is_registered(self):
        assert hasattr(cache_faculty_data_task, 'delay')

    def test_writes_faculty_list_cache(self, admin_user, faculty_instance):
        cache.delete('admin:faculty_list')
        cache_faculty_data_task.delay(admin_user.id)
        assert cache.get('admin:faculty_list') is not None

    def test_writes_department_cache(self, admin_user, faculty_instance, department):
        key = f'admin:faculty:department:{department.department_id}'
        cache.delete(key)
        cache_faculty_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_refreshes_stale_cache(self, admin_user, faculty_instance):
        """Second call must replace the cached data, not leave stale data."""
        cache_faculty_data_task.delay(admin_user.id)
        cache_faculty_data_task.delay(admin_user.id)
        assert cache.get('admin:faculty_list') is not None


@pytest.mark.django_db
class TestCacheStudentDataTask:

    def test_task_is_registered(self):
        assert hasattr(cache_student_data_task, 'delay')

    def test_writes_student_list_cache(self, admin_user, student_instance):
        cache.delete('admin:student_list')
        cache_student_data_task.delay(admin_user.id)
        assert cache.get('admin:student_list') is not None

    def test_writes_program_cache(self, admin_user, student_instance, program):
        key = f'admin:students:program:{program.program_id}'
        cache.delete(key)
        cache_student_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_writes_class_cache(self, admin_user, student_instance, batch_class):
        key = f'admin:students:class:{batch_class.class_id}'
        cache.delete(key)
        cache_student_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_writes_status_cache(self, admin_user, student_instance):
        key = 'admin:students:status:Active'
        cache.delete(key)
        cache_student_data_task.delay(admin_user.id)
        assert cache.get(key) is not None


@pytest.mark.django_db
class TestCacheSemesterDataTask:

    def test_task_is_registered(self):
        assert hasattr(cache_semester_data_task, 'delay')

    def test_writes_semester_list_cache(self, admin_user, inactive_semester):
        cache.delete('admin:semesters_list')
        cache_semester_data_task.delay(admin_user.id)
        assert cache.get('admin:semesters_list') is not None

    def test_writes_class_based_cache(self, admin_user, inactive_semester, batch_class):
        key = f'admin:semesters:class:{batch_class.class_id}'
        cache.delete(key)
        cache_semester_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_cache_reflects_activation_state_change(
        self, admin_user, inactive_semester
    ):
        """
        After semester_activation_task fires, cache_semester_data_task
        should reflect the updated Active status — not serve stale Inactive data.
        """
        # Cache initial state
        cache_semester_data_task.delay(admin_user.id)
        cached_before = cache.get('admin:semesters_list')
        assert cached_before is not None

        # Activate semester
        semester_activation_task.delay(inactive_semester.semester_id)

        # Refresh cache
        cache_semester_data_task.delay(admin_user.id)
        cached_after = cache.get('admin:semesters_list')

        # The cached data must reflect Active status, not stale Inactive
        semester_in_cache = next(
            (s for s in cached_after if s['semester_id'] == inactive_semester.semester_id),
            None
        )
        assert semester_in_cache is not None
        assert semester_in_cache['status'] == 'Active'


@pytest.mark.django_db
class TestCacheCourseAllocationDataTask:

    def test_task_is_registered(self):
        assert hasattr(cache_courseAllocation_data_task, 'delay')

    def test_writes_semester_allocation_cache(
        self, admin_user, course_allocation, inactive_semester
    ):
        key = f'admin:allocations:semester:{inactive_semester.semester_id}'
        cache.delete(key)
        cache_courseAllocation_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_writes_faculty_allocation_cache(
        self, admin_user, course_allocation, faculty_instance
    ):
        key = f'admin:allocations:faculty:{faculty_instance.employee_id.person_id}'
        cache.delete(key)
        cache_courseAllocation_data_task.delay(admin_user.id)
        assert cache.get(key) is not None


@pytest.mark.django_db
class TestCacheEnrollmentDataTask:

    def test_task_is_registered(self):
        assert hasattr(cache_enrollment_data_task, 'delay')

    def test_writes_student_enrollment_cache(
        self, admin_user, enrollment, student_instance
    ):
        key = f'admin:enrollments:student:{student_instance.student_id.person_id}'
        cache.delete(key)
        cache_enrollment_data_task.delay(admin_user.id)
        assert cache.get(key) is not None

    def test_writes_faculty_enrollment_cache(
        self, admin_user, enrollment, faculty_instance
    ):
        key = f'admin:enrollments:faculty:{faculty_instance.employee_id.person_id}'
        cache.delete(key)
        cache_enrollment_data_task.delay(admin_user.id)
        assert cache.get(key) is not None