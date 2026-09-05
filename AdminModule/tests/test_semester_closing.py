"""
test_semester_closing.py
-------------------------
Closing a semester settles everything in one ordered pass:

    auto-calculate missing results -> generate transcripts -> cascade statuses

The order is load-bearing. Transcript generation reads enrollments while they
are still 'Locked' and refuses a semester already marked 'Completed', so both
steps have to happen before the cascade rather than after.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from Models.models import (
    Assessment, AssessmentChecked, CourseAllocation, Enrollment, Result,
    Semester, SemesterDetails, Transcript,
)
from AdminModule.tasks import semester_closing_task


@pytest.fixture
def locked_semester(db, active_semester, course_allocation, enrollment):
    """A semester at the point of closing: coursework locked, results pending."""
    course_allocation.semester = active_semester
    course_allocation.status = 'Locked'
    course_allocation.save()
    enrollment.allocation = course_allocation
    enrollment.status = 'Locked'
    enrollment.save()
    return active_semester


def _graded(enrollment, gpa='3.50', marks='80.00'):
    Result.objects.filter(enrollment=enrollment).update(
        course_gpa=Decimal(gpa), obtained_marks=Decimal(marks),
    )


@pytest.mark.django_db
class TestClosingOrder:

    def test_transcripts_exist_and_statuses_cascade(
        self, locked_semester, course_allocation, enrollment
    ):
        _graded(enrollment)

        semester_closing_task(locked_semester.semester_id)

        locked_semester.refresh_from_db()
        course_allocation.refresh_from_db()
        enrollment.refresh_from_db()

        assert Transcript.objects.filter(semester=locked_semester).count() == 1
        assert locked_semester.status == 'Completed'
        assert course_allocation.status == 'Completed'
        assert enrollment.status == 'Completed'

    def test_transcript_reflects_the_result(self, locked_semester, enrollment, course):
        _graded(enrollment, gpa='3.00')

        semester_closing_task(locked_semester.semester_id)

        transcript = Transcript.objects.get(semester=locked_semester)
        assert transcript.semester_gpa == Decimal('3.00')
        assert transcript.total_credits == course.credit_hours


@pytest.mark.django_db
class TestAutoCalculationAtClosing:

    def _assessment_with_marks(self, allocation, enrollment, obtained):
        assessment = Assessment.objects.create(
            allocation=allocation, assessment_type='Quiz', assessment_name='Q1',
            assessment_date=timezone.now().date(), weightage=100, total_marks=100,
            student_submission=False,
        )
        AssessmentChecked.objects.create(
            assessment=assessment, enrollment=enrollment, obtained=obtained,
        )
        return assessment

    def test_uncalculated_results_are_calculated(
        self, locked_semester, course_allocation, enrollment
    ):
        """Faculty never ran the calculation — closing does it for them."""
        self._assessment_with_marks(course_allocation, enrollment, 80)
        assert enrollment.result.course_gpa is None

        semester_closing_task(locked_semester.semester_id)

        enrollment.refresh_from_db()
        assert enrollment.result.course_gpa is not None
        assert Transcript.objects.filter(semester=locked_semester).exists()

    def test_missing_marks_count_as_zero(
        self, locked_semester, course_allocation, enrollment
    ):
        """A mark never entered scores nothing — marks are frozen by now, so
        there is nobody left who could fill it in."""
        self._assessment_with_marks(course_allocation, enrollment, None)

        semester_closing_task(locked_semester.semester_id)

        enrollment.refresh_from_db()
        assert enrollment.result.obtained_marks == Decimal('0.00')
        assert enrollment.result.course_gpa == Decimal('0.00')

    def test_already_calculated_results_are_left_alone(
        self, locked_semester, course_allocation, enrollment
    ):
        _graded(enrollment, gpa='3.67', marks='82.00')

        semester_closing_task(locked_semester.semester_id)

        enrollment.refresh_from_db()
        assert enrollment.result.course_gpa == Decimal('3.67')
        assert enrollment.result.obtained_marks == Decimal('82.00')


@pytest.mark.django_db
class TestClosingIsIdempotent:
    """reconcile_lifecycle_states re-fires closing tasks it believes were lost,
    so a second run must not duplicate transcripts or error."""

    def test_second_run_is_a_noop(self, locked_semester, enrollment):
        _graded(enrollment)

        semester_closing_task(locked_semester.semester_id)
        result = semester_closing_task(locked_semester.semester_id)

        assert result == 'Semester already closed'
        assert Transcript.objects.filter(semester=locked_semester).count() == 1

    def test_transcripts_are_not_regenerated(self, locked_semester, enrollment):
        """Transcript has a unique constraint on (student, semester) — a second
        generation would raise rather than quietly duplicate."""
        _graded(enrollment)
        semester_closing_task(locked_semester.semester_id)

        from AdminModule.mixins import TranscriptGenerationMixin
        again = TranscriptGenerationMixin().generate_transcripts(locked_semester)

        assert again == []
        assert Transcript.objects.filter(semester=locked_semester).count() == 1

    def test_missing_semester_is_a_noop(self):
        assert 'closed' in semester_closing_task(999999)


@pytest.mark.django_db
class TestDroppedEnrollmentsDoNotBlock:
    """A dropped course used to block a student's entire transcript: its
    Result row has no GPA, and the missing-results check counted it."""

    def test_dropped_course_is_excluded(
        self, locked_semester, course_allocation, enrollment,
        student_instance, faculty_instance
    ):
        from Models.models import Course
        _graded(enrollment)

        dropped_course = Course.objects.create(
            course_code='ZZ-900', course_name='Dropped', credit_hours=3, lab=False,
        )
        SemesterDetails.objects.create(semester=locked_semester, course=dropped_course)
        dropped_allocation = CourseAllocation.objects.create(
            faculty=faculty_instance, course=dropped_course, semester=locked_semester,
            session=str(locked_semester.session), status='Locked',
        )
        dropped = Enrollment.objects.create(
            student=student_instance, allocation=dropped_allocation, status='Dropped',
        )
        Result.objects.create(enrollment=dropped)   # never graded — they dropped

        semester_closing_task(locked_semester.semester_id)

        transcript = Transcript.objects.get(semester=locked_semester)
        # only the course they actually took counts toward the GPA
        assert transcript.total_credits == enrollment.allocation.course.credit_hours


@pytest.mark.django_db
class TestGradingPolicy:
    """Handbook grading. Under 20 students -> absolute (Table 3); 20 or more ->
    relative. In both, marks below the passing threshold fail AND are excluded
    from the cohort before mean and SD are computed."""

    def _cohort(self, allocation, marks):
        """Enroll one student per mark and record it as a single 100% assessment."""
        from Models.models import Person, Student
        from django.contrib.auth.models import User
        from datetime import date

        assessment = Assessment.objects.create(
            allocation=allocation, assessment_type='Final', assessment_name='F',
            assessment_date=timezone.now().date(), weightage=100, total_marks=100,
            student_submission=False,
        )
        enrollments = []
        for i, mark in enumerate(marks):
            user = User.objects.create_user(username=f'grade{i}@test.com', password='x')
            person = Person.objects.create(
                person_id=f'GRD-{i:03d}', first_name=f'S{i}', last_name='T',
                father_name='F', gender='Male', dob=date(2002, 1, 1),
                cnic=f'11111-{i:07d}-1', contact_number=f'+9230011111{i:02d}',
                institutional_email=f'grade{i}@test.com', type='Student', user=user,
            )
            student = Student.objects.create(
                student_id=person,
                program=allocation.semester.associated_class.program,
                student_class=allocation.semester.associated_class,
                admission_date=date(2023, 1, 1), status='Active',
            )
            enrollment = Enrollment.objects.create(
                student=student, allocation=allocation, status='Locked',
            )
            Result.objects.create(enrollment=enrollment)
            AssessmentChecked.objects.create(
                assessment=assessment, enrollment=enrollment, obtained=mark,
            )
            enrollments.append(enrollment)
        return enrollments

    # -- absolute (class under 20) ------------------------------------------

    @pytest.mark.parametrize('mark,expected', [
        (95, '4.00'), (90, '4.00'), (84, '3.67'), (79, '3.33'), (74, '3.00'),
        (70, '2.67'), (67, '2.33'), (63, '2.00'), (60, '1.67'), (56, '1.33'),
        (52, '1.00'), (49, '0.00'),
    ])
    def test_absolute_bands_match_table_3(
        self, locked_semester, course_allocation, enrollment, mark, expected
    ):
        Assessment.objects.create(
            allocation=course_allocation, assessment_type='Final', assessment_name='F',
            assessment_date=timezone.now().date(), weightage=100, total_marks=100,
            student_submission=False,
        ).assessmentchecked_set.create(enrollment=enrollment, obtained=mark)

        from AdminModule.mixins import ResultCalculationMixin
        ResultCalculationMixin().calculate_result(course_allocation)

        enrollment.refresh_from_db()
        assert enrollment.result.course_gpa == Decimal(expected)

    def test_d_plus_band_exists(self, locked_semester, course_allocation, enrollment):
        """54-57.99 -> D+ (1.33). This band was missing from the ladder entirely,
        so marks in it were being awarded 1.00 instead."""
        Assessment.objects.create(
            allocation=course_allocation, assessment_type='Final', assessment_name='F',
            assessment_date=timezone.now().date(), weightage=100, total_marks=100,
            student_submission=False,
        ).assessmentchecked_set.create(enrollment=enrollment, obtained=55)

        from AdminModule.mixins import ResultCalculationMixin
        ResultCalculationMixin().calculate_result(course_allocation)

        enrollment.refresh_from_db()
        assert enrollment.result.course_gpa == Decimal('1.33')

    # -- relative (class of 20 or more) -------------------------------------

    def test_relative_grading_kicks_in_at_twenty(self, locked_semester, course_allocation):
        from AdminModule.mixins import ResultCalculationMixin
        self._cohort(course_allocation, [70] * 20)

        data = ResultCalculationMixin().calculate_result(course_allocation)

        # the relative branch reports the cohort statistics
        assert 'mean' in data and 'standard_deviation' in data

    def test_failing_marks_are_excluded_from_the_statistics(
        self, locked_semester, course_allocation
    ):
        """The handbook: students below the threshold "are not included for
        assigning grades". Excluding them lifts the mean, which is why moving
        the threshold moves everyone's grade."""
        from AdminModule.mixins import ResultCalculationMixin
        marks = [20, 30, 40] + [70] * 20      # three well below a 50 threshold
        self._cohort(course_allocation, marks)

        data = ResultCalculationMixin().calculate_result(course_allocation)

        # mean of the passing 20 only, not of all 23
        assert data['mean'] == pytest.approx(70.0)

    def test_raising_the_threshold_shifts_the_curve(
        self, locked_semester, course_allocation
    ):
        """Same marks, different threshold: the weakest drop out of the cohort,
        the mean rises, and the remaining students' z-scores change."""
        from AdminModule.mixins import ResultCalculationMixin
        marks = [45, 46, 47] + [60] * 10 + [75] * 10
        self._cohort(course_allocation, marks)

        course_allocation.passing_threshold = 40
        course_allocation.save()
        low = ResultCalculationMixin().calculate_result(course_allocation)

        course_allocation.passing_threshold = 50
        course_allocation.save()
        high = ResultCalculationMixin().calculate_result(course_allocation)

        assert high['mean'] > low['mean']

    def test_marks_below_threshold_fail_regardless_of_the_curve(
        self, locked_semester, course_allocation
    ):
        """A weak cohort must not lift a failing mark into a passing grade."""
        from AdminModule.mixins import ResultCalculationMixin
        enrollments = self._cohort(course_allocation, [45] + [30] * 20)

        ResultCalculationMixin().calculate_result(course_allocation)

        top_of_a_weak_class = enrollments[0]
        top_of_a_weak_class.refresh_from_db()
        assert top_of_a_weak_class.result.course_gpa == Decimal('0.00')


@pytest.mark.django_db
class TestSkippedTranscriptNotification:

    def test_admin_is_told_when_a_transcript_is_skipped(
        self, locked_semester, course_allocation, enrollment, admin_instance, course
    ):
        from Models.models import Notification
        _graded(enrollment)
        course.credit_hours = 0     # nothing to weight the average by
        course.save()

        semester_closing_task(locked_semester.semester_id)

        assert not Transcript.objects.filter(semester=locked_semester).exists()
        note = Notification.objects.filter(verb='transcript_skipped').first()
        assert note is not None
        assert note.level == 'action_required'
        assert enrollment.student.student_id.person_id in note.message

    def test_no_notification_when_everything_generated(
        self, locked_semester, enrollment, admin_instance
    ):
        from Models.models import Notification
        _graded(enrollment)

        semester_closing_task(locked_semester.semester_id)

        assert Transcript.objects.filter(semester=locked_semester).exists()
        assert not Notification.objects.filter(verb='transcript_skipped').exists()
