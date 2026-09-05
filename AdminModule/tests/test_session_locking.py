"""
test_session_locking.py
------------------------
Setting a session's closing_deadline is the admin action that freezes the
session's coursework:

  - every Active allocation and enrollment under it moves to 'Locked'
  - three escalating pending-results reminders are scheduled (2d / 1d / 6h)

Locking is deliberately admin-driven so the lifecycle never waits on whether a
teacher gets round to calculating results.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from Models.models import (
    AcademicSession, CourseAllocation, Enrollment, Notification,
)


@pytest.fixture
def closing_session(db, active_semester):
    """An Active session, which is the only state where closing_deadline is
    writable (see SessionSerializer.get_extra_kwargs)."""
    session = active_semester.session
    session.status = 'Active'
    session.activation_deadline = timezone.now() - timedelta(days=30)
    session.closing_deadline = timezone.now() + timedelta(days=20)
    session.save()
    return session


def _patch_deadline(admin_client, session, when):
    with patch('AdminModule.tasks.session_closing_task.apply_async') as closing, \
         patch('AdminModule.tasks.pending_results_reminder_task.apply_async') as reminder, \
         patch('AdminModule.tasks.session_locking_task.delay') as locking:
        closing.return_value.id = 'closing-id'
        reminder.return_value.id = 'reminder-id'
        response = admin_client.patch(
            reverse('Admin:session-detail', kwargs={'id': session.id}),
            {'closing_deadline': when.isoformat()}, format='json',
        )
    return response, closing, reminder, locking


@pytest.mark.django_db
class TestClosingDeadlineSchedulesWork:

    def test_locking_task_is_fired(self, admin_client, closing_session):
        r, _, _, locking = _patch_deadline(
            admin_client, closing_session, timezone.now() + timedelta(days=20)
        )
        assert r.status_code == 200
        locking.assert_called_once_with(closing_session.id)

    def test_three_reminders_scheduled(self, admin_client, closing_session):
        deadline = timezone.now() + timedelta(days=20)
        r, _, reminder, _ = _patch_deadline(admin_client, closing_session, deadline)
        assert r.status_code == 200
        assert reminder.call_count == 3
        phrases = [call.kwargs['args'][1] for call in reminder.call_args_list]
        assert phrases == ['2 days', '1 day', '6 hours']

    def test_reminders_fire_before_the_deadline(self, admin_client, closing_session):
        deadline = timezone.now() + timedelta(days=20)
        _, _, reminder, _ = _patch_deadline(admin_client, closing_session, deadline)
        for call in reminder.call_args_list:
            assert call.kwargs['eta'] < deadline

    def test_rescheduling_revokes_old_reminders(self, admin_client, closing_session):
        _patch_deadline(admin_client, closing_session, timezone.now() + timedelta(days=20))
        with patch('AdminModule.serializers.app.control.revoke') as revoke:
            _patch_deadline(admin_client, closing_session, timezone.now() + timedelta(days=25))
        revoked = revoke.call_count
        # one for the closing task, three for the reminders
        assert revoked >= 4


@pytest.mark.django_db
class TestSessionLockingTask:

    def test_active_allocations_and_enrollments_lock(
        self, closing_session, course_allocation, enrollment
    ):
        from AdminModule.tasks import session_locking_task
        course_allocation.semester = closing_session.semester_set.first()
        course_allocation.status = 'Active'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()

        session_locking_task(closing_session.id)

        course_allocation.refresh_from_db()
        enrollment.refresh_from_db()
        assert course_allocation.status == 'Locked'
        assert enrollment.status == 'Locked'

    def test_completed_rows_are_left_alone(
        self, closing_session, course_allocation, enrollment
    ):
        from AdminModule.tasks import session_locking_task
        course_allocation.semester = closing_session.semester_set.first()
        course_allocation.status = 'Completed'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Dropped'
        enrollment.save()

        session_locking_task(closing_session.id)

        course_allocation.refresh_from_db()
        enrollment.refresh_from_db()
        assert course_allocation.status == 'Completed'
        assert enrollment.status == 'Dropped'

    def test_missing_session_is_a_noop(self):
        from AdminModule.tasks import session_locking_task
        assert 'locked' in session_locking_task(999999)


@pytest.mark.django_db
class TestPendingResultsReminderTask:

    def _setup(self, session, allocation, enrollment):
        allocation.semester = session.semester_set.first()
        allocation.status = 'Locked'
        allocation.save()
        enrollment.allocation = allocation
        enrollment.save()
        return allocation

    def test_reports_allocations_missing_results(
        self, closing_session, course_allocation, enrollment, admin_instance
    ):
        from AdminModule.tasks import pending_results_reminder_task
        self._setup(closing_session, course_allocation, enrollment)
        enrollment.result.course_gpa = None
        enrollment.result.save()

        with patch('AdminModule.email_service.send_mail') as mail:
            result = pending_results_reminder_task(closing_session.id, '2 days')

        assert '1 allocation(s) pending' in result
        assert mail.call_count >= 2  # faculty + admin
        assert Notification.objects.filter(verb='result_calculation_pending').exists()

    def test_silent_when_every_result_exists(
        self, closing_session, course_allocation, enrollment, admin_instance
    ):
        from AdminModule.tasks import pending_results_reminder_task
        from decimal import Decimal
        self._setup(closing_session, course_allocation, enrollment)
        enrollment.result.course_gpa = Decimal('3.00')
        enrollment.result.save()

        with patch('AdminModule.email_service.send_mail') as mail:
            result = pending_results_reminder_task(closing_session.id, '1 day')

        assert result == 'No pending results'
        mail.assert_not_called()

    def test_noop_once_the_session_has_closed(
        self, closing_session, course_allocation, enrollment
    ):
        from AdminModule.tasks import pending_results_reminder_task
        self._setup(closing_session, course_allocation, enrollment)
        closing_session.status = 'Completed'
        closing_session.save()

        with patch('AdminModule.email_service.send_mail') as mail:
            result = pending_results_reminder_task(closing_session.id, '6 hours')

        assert result == 'Session already closed'
        mail.assert_not_called()

    def test_noop_when_session_never_had_a_deadline(self, db):
        """Clearing a closing deadline is blocked by the database trigger, so
        the only way to reach this guard is a session that never had one."""
        from AdminModule.tasks import pending_results_reminder_task
        session = AcademicSession.objects.create(
            period='Spring', year=2087, status='Active',
        )
        with patch('AdminModule.email_service.send_mail') as mail:
            result = pending_results_reminder_task(session.id, '6 hours')

        assert result == 'No closing deadline set'
        mail.assert_not_called()


@pytest.mark.django_db
class TestClosingDeadlineBounds:
    """A closing deadline must sit between 1 and 4 weeks out — far enough for
    the reminder ladder to run, near enough that a typo cannot park a session
    years away — and cannot be cleared once the locking cascade depends on it."""

    def _patch(self, admin_client, session, when):
        with patch('AdminModule.tasks.session_closing_task.apply_async') as closing, \
             patch('AdminModule.tasks.pending_results_reminder_task.apply_async') as reminder, \
             patch('AdminModule.tasks.session_locking_task.delay'):
            closing.return_value.id = 'x'
            reminder.return_value.id = 'y'
            return admin_client.patch(
                reverse('Admin:session-detail', kwargs={'id': session.id}),
                {'closing_deadline': when.isoformat() if when is not None else None},
                format='json',
            )

    def test_less_than_a_week_rejected(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, timezone.now() + timedelta(days=6))
        assert r.status_code == 400
        assert 'at least 1 week' in str(r.data)

    def test_exactly_over_a_week_accepted(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, timezone.now() + timedelta(days=7, hours=1))
        assert r.status_code == 200

    def test_more_than_four_weeks_rejected(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, timezone.now() + timedelta(weeks=5))
        assert r.status_code == 400
        assert 'more than 4 weeks' in str(r.data)

    def test_just_under_four_weeks_accepted(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, timezone.now() + timedelta(weeks=4) - timedelta(hours=1))
        assert r.status_code == 200

    def test_past_deadline_rejected(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, timezone.now() - timedelta(days=1))
        assert r.status_code == 400

    def test_cannot_be_cleared(self, admin_client, closing_session):
        r = self._patch(admin_client, closing_session, None)
        assert r.status_code == 400
        assert 'cannot be cleared' in str(r.data)
        closing_session.refresh_from_db()
        assert closing_session.closing_deadline is not None

    def test_every_reminder_has_room_within_the_minimum(self, admin_client, closing_session):
        """The 1-week floor exists so all three nudges can actually fire."""
        deadline = timezone.now() + timedelta(days=7, hours=1)
        with patch('AdminModule.tasks.session_closing_task.apply_async') as closing, \
             patch('AdminModule.tasks.pending_results_reminder_task.apply_async') as reminder, \
             patch('AdminModule.tasks.session_locking_task.delay'):
            closing.return_value.id = 'x'
            reminder.return_value.id = 'y'
            admin_client.patch(
                reverse('Admin:session-detail', kwargs={'id': closing_session.id}),
                {'closing_deadline': deadline.isoformat()}, format='json',
            )
        assert reminder.call_count == 3


@pytest.mark.django_db
class TestActivationDeadlineBounds:
    """Activation must sit 2–4 weeks out. The 2-week floor exists so the
    default 1-week availability window still has a full week of runway."""

    @pytest.fixture
    def fresh_session(self, db):
        return AcademicSession.objects.create(period='Spring', year=2099, status='Inactive')

    def _patch(self, admin_client, session, when):
        with patch('AdminModule.tasks.session_activation_task.apply_async') as act, \
             patch('AdminModule.tasks.session_availability_task.apply_async') as avail:
            act.return_value.id = 'a'
            avail.return_value.id = 'b'
            return admin_client.patch(
                reverse('Admin:session-detail', kwargs={'id': session.id}),
                {'activation_deadline': when.isoformat()}, format='json',
            )

    def test_under_two_weeks_rejected(self, admin_client, fresh_session):
        r = self._patch(admin_client, fresh_session, timezone.now() + timedelta(days=13))
        assert r.status_code == 400
        assert 'at least 2 weeks' in str(r.data)

    def test_just_over_two_weeks_accepted(self, admin_client, fresh_session):
        r = self._patch(admin_client, fresh_session, timezone.now() + timedelta(days=14, hours=1))
        assert r.status_code == 200

    def test_over_four_weeks_rejected(self, admin_client, fresh_session):
        r = self._patch(admin_client, fresh_session, timezone.now() + timedelta(weeks=5))
        assert r.status_code == 400

    def test_availability_window_lands_in_the_future(self, admin_client, fresh_session):
        """The 2-week floor guarantees this — the window must never open in the past."""
        self._patch(admin_client, fresh_session, timezone.now() + timedelta(days=14, hours=1))
        fresh_session.refresh_from_db()
        assert fresh_session.availability_deadline > timezone.now()


@pytest.mark.django_db
class TestAvailabilityDeltaRules:
    """The delta only means something relative to a pending activation deadline."""

    @pytest.fixture
    def fresh_session(self, db):
        return AcademicSession.objects.create(period='Spring', year=2098, status='Inactive')

    def _patch(self, admin_client, session, delta):
        with patch('AdminModule.tasks.session_availability_task.apply_async') as avail:
            avail.return_value.id = 'b'
            return admin_client.patch(
                reverse('Admin:session-detail', kwargs={'id': session.id}),
                {'availability_delta': delta}, format='json',
            )

    def test_frozen_when_no_activation_deadline(self, admin_client, fresh_session):
        assert fresh_session.activation_deadline is None
        r = self._patch(admin_client, fresh_session, 5)
        assert r.status_code == 400
        assert 'activation deadline' in str(r.data)

    def test_frozen_once_activation_has_passed(self, admin_client, fresh_session):
        fresh_session.activation_deadline = timezone.now() - timedelta(days=1)
        fresh_session.save()
        r = self._patch(admin_client, fresh_session, 5)
        assert r.status_code == 400
        assert 'has passed' in str(r.data)

    def test_delta_cannot_reach_past_the_activation_deadline(self, admin_client, fresh_session):
        fresh_session.activation_deadline = timezone.now() + timedelta(days=20)
        fresh_session.save()
        r = self._patch(admin_client, fresh_session, 30)   # window would open 10 days ago
        assert r.status_code == 400
        assert 'before now' in str(r.data)

    def test_valid_delta_accepted_and_reschedules(self, admin_client, fresh_session):
        fresh_session.activation_deadline = timezone.now() + timedelta(days=20)
        fresh_session.save()
        with patch('AdminModule.tasks.session_availability_task.apply_async') as avail:
            avail.return_value.id = 'b'
            r = admin_client.patch(
                reverse('Admin:session-detail', kwargs={'id': fresh_session.id}),
                {'availability_delta': 10}, format='json',
            )
        assert r.status_code == 200
        fresh_session.refresh_from_db()
        assert fresh_session.availability_delta == 10
        # the queued availability task must be re-aimed at the new moment
        avail.assert_called_once()
        assert avail.call_args.kwargs['eta'] == fresh_session.availability_deadline


@pytest.mark.django_db(transaction=True)
class TestDeadlineTriggers:
    """The lifecycle hangs off these deadlines, and every write path outside
    the API — shell, data migrations, bulk updates — bypasses the serializers.
    MySQL triggers hold the same rules (see migration 0019)."""

    def test_forward_scheduling_inside_the_window_is_accepted(self):
        s = AcademicSession.objects.create(
            period='Fall', year=2091, status='Inactive',
            activation_deadline=timezone.now() + timedelta(days=20),
        )
        assert s.pk is not None

    def test_too_soon_blocked_at_the_database(self):
        from django.db.utils import OperationalError
        with pytest.raises(OperationalError) as exc:
            AcademicSession.objects.create(
                period='Fall', year=2090, status='Inactive',
                activation_deadline=timezone.now() + timedelta(days=3),
            )
        assert exc.value.args[0] == 1644
        assert 'at least 2 weeks' in exc.value.args[1]

    def test_too_far_blocked_at_the_database(self):
        from django.db.utils import OperationalError
        with pytest.raises(OperationalError):
            AcademicSession.objects.create(
                period='Fall', year=2089, status='Inactive',
                activation_deadline=timezone.now() + timedelta(weeks=6),
            )

    def test_past_deadlines_remain_writable(self):
        """Writing a past date records history rather than scheduling, so the
        minimum-window rule has nothing to say about it — fixtures that
        simulate an already-running session depend on this."""
        s = AcademicSession.objects.create(
            period='Spring', year=2088, status='Active',
            activation_deadline=timezone.now() - timedelta(days=30),
        )
        assert s.pk is not None

    def test_queryset_update_is_caught_too(self):
        """The path a model manager would miss — .update() skips save()."""
        from django.db.utils import OperationalError
        s = AcademicSession.objects.create(
            period='Fall', year=2087, status='Active',
            activation_deadline=timezone.now() + timedelta(days=20),
            closing_deadline=timezone.now() + timedelta(days=25),
        )
        with pytest.raises(OperationalError) as exc:
            AcademicSession.objects.filter(pk=s.pk).update(closing_deadline=None)
        assert 'cannot be cleared' in exc.value.args[1]


@pytest.mark.django_db(transaction=True)
class TestTriggerErrorsBecomeValidationErrors:
    """A trigger rejection is an OperationalError, which DRF would surface as a
    500 — the custom exception handler turns it into an ordinary 400."""

    def test_handler_translates_signal_to_400(self):
        from django.db.utils import OperationalError
        from NexusAPI.exception_handlers import api_exception_handler

        exc = OperationalError(1644, 'Closing deadline cannot be cleared.')
        response = api_exception_handler(exc, {'view': None})

        assert response is not None
        assert response.status_code == 400
        assert response.data == {'detail': 'Closing deadline cannot be cleared.'}

    def test_other_database_faults_still_bubble_up(self):
        """Only SIGNAL 1644 is a rule violation; a real fault must stay a 500."""
        from django.db.utils import OperationalError
        from NexusAPI.exception_handlers import api_exception_handler

        exc = OperationalError(2006, 'MySQL server has gone away')
        assert api_exception_handler(exc, {'view': None}) is None


@pytest.mark.django_db
class TestResultCalculationInvalidatesCaches:
    """Calculating results changes grades and enrollment counts, so every
    cached view of them is stale. Nothing was invalidated here before."""

    def _apply_request(self, faculty_client, change_request):
        from django.urls import reverse as drf_reverse
        # A request must be admin-confirmed before faculty can apply it —
        # `status` is read-only while it is still pending.
        change_request.status = 'confirmed'
        change_request.save()
        return faculty_client.patch(
            drf_reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk}),
            {'status': 'applied'}, format='json',
        )

    def test_admin_cache_tasks_are_refired(self, faculty_client, change_request, assessment, enrollment):
        from django.core.cache import cache
        AssessmentChecked = enrollment.assessmentchecked_set.model
        AssessmentChecked.objects.create(
            assessment=assessment, enrollment=enrollment, obtained=70,
        )
        with patch('AdminModule.tasks.cache_courseAllocation_data_task.delay') as alloc, \
             patch('AdminModule.tasks.cache_enrollment_data_task.delay') as enrol:
            self._apply_request(faculty_client, change_request)

        alloc.assert_called_once()
        enrol.assert_called_once()

    def test_faculty_and_student_keys_are_cleared(
        self, faculty_client, faculty_instance, change_request, assessment, enrollment
    ):
        from django.core.cache import cache
        AssessmentChecked = enrollment.assessmentchecked_set.model
        AssessmentChecked.objects.create(
            assessment=assessment, enrollment=enrollment, obtained=70,
        )
        username = faculty_instance.employee_id.user.username
        allocation_id = change_request.target_allocation.allocation_id
        student_username = enrollment.student.student_id.user.username

        cache.set(f'faculty:{username}:allocations', ['stale'], 300)
        cache.set(f'faculty:{username}:{allocation_id}:assessments', ['stale'], 300)
        cache.set(f'student:dashboard:{student_username}', {'stale': True}, 300)

        with patch('AdminModule.tasks.cache_courseAllocation_data_task.delay'), \
             patch('AdminModule.tasks.cache_enrollment_data_task.delay'):
            self._apply_request(faculty_client, change_request)

        assert cache.get(f'faculty:{username}:allocations') is None
        assert cache.get(f'faculty:{username}:{allocation_id}:assessments') is None
        assert cache.get(f'student:dashboard:{student_username}') is None


@pytest.mark.django_db
class TestTranscriptUrlGate:
    """The transcript link only appears once the session has a closing deadline
    — before that, results are still being entered."""

    def test_hidden_without_a_closing_deadline(self, admin_client, inactive_semester):
        assert inactive_semester.session.closing_deadline is None
        r = admin_client.get(
            reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        )
        assert r.status_code == 200
        assert 'transcript_generation_url' not in r.data

    def test_shown_once_a_closing_deadline_exists(self, admin_client, active_semester):
        session = active_semester.session
        session.closing_deadline = timezone.now() + timedelta(days=20)
        session.save()

        r = admin_client.get(
            reverse('Admin:semester-detail', kwargs={'semester_id': active_semester.semester_id})
        )
        assert r.status_code == 200
        assert 'transcript_generation_url' in r.data
