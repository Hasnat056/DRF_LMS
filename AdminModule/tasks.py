import logging
from itertools import groupby
from celery import shared_task
from django.core.mail import send_mail
from django.db import transaction
from rest_framework.generics import get_object_or_404

from Models.models import *
from django.core.cache import cache
from .serializers import FacultySerializer, StudentSerializer, ProgramSerializer, CourseSerializer, SemesterSerializer, \
    CourseAllocationSerializer, EnrollmentSerializer
from .mixins import ResultCalculationMixin, TranscriptGenerationMixin
from . import email_service

from django.conf import settings
from django.http import QueryDict
from django.utils.encoding import iri_to_uri
from django.utils import timezone

logger = logging.getLogger(__name__)


class CustomRequest:

    def __init__(self, user=None, method='GET', base_url=None, query_params=None):
        self.user = user
        self.method = method
        self.base_url = base_url or getattr(settings, 'BASE_URL', 'http://localhost:8000')

        # DRF looks for both .query_params and .GET
        self.query_params = query_params or {}
        self.GET = QueryDict('', mutable=True)
        for key, value in self.query_params.items():
            self.GET[key] = value

    def build_absolute_uri(self, location=None):

        if location is None:
            return self.base_url

        # Already absolute
        if location.startswith(('http://', 'https://', '//')):
            return iri_to_uri(location)

        return iri_to_uri(f"{self.base_url}{location}")



@shared_task
@transaction.atomic
def semester_activation_task(semester_id):
    semester = Semester.objects.filter(semester_id=semester_id).prefetch_related('courseallocation_set').prefetch_related('courseallocation_set__enrollment_set').first()
    if not semester:
        logger.warning('semester_activation_task fired for missing semester_id=%s', semester_id)
        return f'Semester {semester_id} has been activated successfully!'

    if semester.status == 'Active':
        logger.debug('semester_activation_task: semester_id=%s already Active, no-op', semester_id)
        return "Semester already activated"

    semester.status = 'Active'
    semester.save()
    allocation_count = 0
    for each in semester.courseallocation_set.all():
        each.status = 'Active'
        for enroll in each.enrollment_set.all():
            enroll.status = 'Active'
            enroll.save()
        each.save()
        allocation_count += 1

    logger.info(
        'Semester %s activated, cascaded to %s course allocation(s)', semester_id, allocation_count
    )
    return f'Semester {semester_id} has been activated successfully!'

@shared_task
@transaction.atomic
def semester_closing_task(semester_id):
    """Close a semester: settle results, issue transcripts, then cascade.

    The order matters. Transcript generation refuses a semester that is already
    Completed, and it reads enrollments while they are still Locked — so both
    must happen before the status cascade, not after.

    Any allocation whose results the faculty never calculated is calculated
    here, with missing marks counting as zero. By this point marks are frozen
    and nobody is left who could fill them in, so closing cannot wait.
    """
    semester = Semester.objects.filter(semester_id=semester_id).prefetch_related(
        'courseallocation_set').prefetch_related('courseallocation_set__enrollment_set').first()
    if not semester:
        logger.warning('semester_closing_task fired for missing semester_id=%s', semester_id)
        return f'Semester {semester_id} has been closed successfully!'

    if semester.status == 'Completed':
        logger.debug('semester_closing_task: semester_id=%s already closed, no-op', semester_id)
        return "Semester already closed"

    calculator = ResultCalculationMixin()
    auto_calculated = []
    for allocation in semester.courseallocation_set.all():
        if allocation.enrollment_set.filter(result__course_gpa__isnull=True).exists():
            calculator.calculate_result(allocation)
            auto_calculated.append(allocation.allocation_id)

    if auto_calculated:
        logger.warning(
            'semester_closing_task: results were not calculated by faculty for '
            'allocation(s) %s in semester_id=%s — calculated automatically at closing',
            auto_calculated, semester_id,
        )

    try:
        TranscriptGenerationMixin().generate_transcripts(semester)
    except ValueError as missing:
        # Results should exist by now; if any are still missing the semester is
        # left open rather than closed without transcripts.
        logger.error(
            'semester_closing_task: cannot close semester_id=%s, results still missing: %s',
            semester_id, missing.args[0],
        )
        raise

    semester.status = 'Completed'
    semester.save()
    allocation_count = 0
    for each in semester.courseallocation_set.all():
        each.status = 'Completed'
        for enroll in each.enrollment_set.all():
            enroll.status = 'Completed'
            enroll.save()
        each.save()
        allocation_count += 1

    logger.info(
        'Semester %s closed, cascaded to %s course allocation(s)', semester_id, allocation_count
    )
    return f'Semester {semester_id} has been closed successfully!'


@shared_task
def session_activation_task(session_id):
    session = AcademicSession.objects.filter(id=session_id).first()
    if not session:
        logger.warning('session_activation_task fired for missing session_id=%s', session_id)
        return f'Session {session_id} has been activated successfully!'

    if session.status == 'Active':
        logger.debug('session_activation_task: session_id=%s already Active, no-op', session_id)
        return "Session already activated"

    session.status = 'Active'
    session.save()
    semester_ids = list(Semester.objects.filter(session_id=session_id).values_list('semester_id', flat=True))
    for semester_id in semester_ids:
        semester_activation_task.delay(semester_id)

    logger.info('Session %s activated, cascaded to %s semester(s)', session_id, len(semester_ids))
    return f'Session {session_id} has been activated successfully!'


@shared_task
def session_availability_task(session_id):
    session = AcademicSession.objects.filter(id=session_id).first()
    if not session:
        logger.warning('session_availability_task fired for missing session_id=%s', session_id)
        return f'Session {session_id} availability window has opened'

    if session.status != 'Initiated':
        logger.debug(
            'session_availability_task: session_id=%s status=%s, expected Initiated, no-op',
            session_id, session.status
        )
        return "Session not in Initiated state"

    session.status = 'Available'
    session.save()
    semester_ids = list(Semester.objects.filter(session_id=session_id).values_list('semester_id', flat=True))
    for semester_id in semester_ids:
        cache_semester_enrollment_data_task.delay(semester_id)

    logger.info(
        'Session %s availability window opened, refreshed enrollment cache for %s semester(s)',
        session_id, len(semester_ids)
    )
    return f'Session {session_id} availability window has opened'


@shared_task
@transaction.atomic
def session_locking_task(session_id):
    """Freeze a session's coursework once an admin sets its closing_deadline.

    Every Active allocation and enrollment under the session moves to 'Locked':
    assessments, totals and obtained marks stop moving. Faculty can still
    calculate results and adjust passing_threshold — that is the point of the
    locked window. 'Completed' comes later, from semester_closing_task.

    Locking is the admin's action deliberately: it must not depend on whether
    a teacher ever gets round to calculating results.
    """
    session = AcademicSession.objects.filter(id=session_id).first()
    if not session:
        logger.warning('session_locking_task fired for missing session_id=%s', session_id)
        return f'Session {session_id} has been locked'

    allocations = CourseAllocation.objects.filter(
        semester__session_id=session_id, status='Active'
    ).update(status='Locked')

    enrollments = Enrollment.objects.filter(
        allocation__semester__session_id=session_id, status='Active'
    ).update(status='Locked')

    logger.info(
        'Session %s locked: %s allocation(s), %s enrollment(s)',
        session_id, allocations, enrollments,
    )
    return f'Session {session_id} has been locked'


@shared_task
def pending_results_reminder_task(session_id, remaining):
    """Nudge admin and faculty about allocations with no results yet.

    Scheduled at three points before the closing deadline (2 days, 1 day,
    6 hours). `remaining` is the human phrase used in the subject line.
    Nothing is calculated here — this only reports.
    """
    session = AcademicSession.objects.filter(id=session_id).first()
    if not session:
        logger.warning('pending_results_reminder_task fired for missing session_id=%s', session_id)
        return f'Session {session_id} not found'

    if session.status == 'Completed':
        logger.debug('pending_results_reminder_task: session_id=%s already closed, no-op', session_id)
        return 'Session already closed'

    if not session.closing_deadline:
        # The deadline was cleared after this reminder was scheduled — there is
        # nothing left to remind anyone about.
        logger.debug(
            'pending_results_reminder_task: session_id=%s has no closing_deadline, no-op',
            session_id,
        )
        return 'No closing deadline set'

    pending = []
    allocations = (
        CourseAllocation.objects
        .filter(semester__session_id=session_id)
        .exclude(status__in=['Completed', 'Cancelled'])
        .select_related('course', 'semester', 'faculty__employee_id')
    )
    for allocation in allocations:
        missing = allocation.enrollment_set.filter(result__course_gpa__isnull=True).count()
        if missing:
            pending.append((allocation, missing))

    if not pending:
        logger.info('pending_results_reminder_task: session_id=%s has no pending results', session_id)
        return 'No pending results'

    for allocation, missing in pending:
        if allocation.faculty.employee_id.institutional_email:
            email_service.send_pending_results_to_faculty(session, allocation, missing, remaining)

        if allocation.faculty.employee_id.user_id:
            Notification.objects.create(
                recipient=allocation.faculty.employee_id.user,
                verb='result_calculation_pending',
                message=(
                    f'{allocation.course.course_code}: {missing} result(s) pending, '
                    f'session closes in {remaining}.'
                ),
                level='action_required',
                content_type=ContentType.objects.get_for_model(CourseAllocation),
                object_id=allocation.pk,
            )

    for admin in Admin.objects.filter(status='Active').select_related('employee_id__user'):
        if admin.employee_id.institutional_email:
            email_service.send_pending_results_to_admin(
                session, pending, admin.employee_id.institutional_email, remaining,
            )
        if admin.employee_id.user_id:
            Notification.objects.create(
                recipient=admin.employee_id.user,
                verb='result_calculation_pending',
                message=(
                    f'{len(pending)} allocation(s) still without results — '
                    f'{session} closes in {remaining}.'
                ),
                level='action_required',
                content_type=ContentType.objects.get_for_model(AcademicSession),
                object_id=session.pk,
            )

    logger.info(
        'pending_results_reminder_task: session_id=%s, %s allocation(s) pending, %s left',
        session_id, len(pending), remaining,
    )
    return f'{len(pending)} allocation(s) pending'


@shared_task
def session_closing_task(session_id):
    session = AcademicSession.objects.filter(id=session_id).first()
    if not session:
        logger.warning('session_closing_task fired for missing session_id=%s', session_id)
        return f'Session {session_id} has been closed successfully!'

    session.status = 'Completed'
    session.save()
    semester_ids = list(Semester.objects.filter(session_id=session_id).values_list('semester_id', flat=True))
    for semester_id in semester_ids:
        semester_closing_task.delay(semester_id)

    logger.info('Session %s closed, cascaded to %s semester(s)', session_id, len(semester_ids))
    return f'Session {session_id} has been closed successfully!'


@shared_task
def reconcile_lifecycle_states():
    """
    Safety-net sweep for the eta-scheduled activation/availability/closing tasks.
    If a scheduled task is ever lost (broker restart, worker crash, etc.), this
    catches any Semester/AcademicSession whose deadline has already passed but
    whose status never advanced, and re-fires the same task that should have
    fired originally. All of those tasks already guard against double-firing,
    so re-triggering here is always safe.

    Finding anything to fix here means an eta-scheduled task was lost upstream —
    that's logged at WARNING since it's a symptom of a real problem (broker
    restart, worker crash, etc.), not routine behavior.
    """
    now = timezone.now()
    caught = []

    # Semesters have no deadlines of their own — they follow their session.
    # A semester still Inactive under an Active session (or still Active under
    # a Completed one) means the cascade fired but its per-semester task was
    # lost, so re-fire it. Both tasks are idempotent.
    for semester in Semester.objects.filter(status='Inactive', session__status='Active'):
        semester_activation_task.delay(semester.semester_id)
        caught.append(f'semester {semester.semester_id} activation')

    for semester in Semester.objects.filter(status='Active', session__status='Completed'):
        semester_closing_task.delay(semester.semester_id)
        caught.append(f'semester {semester.semester_id} closing')

    for session in AcademicSession.objects.filter(status='Initiated', activation_deadline__isnull=False, activation_deadline__lte=now):
        session_activation_task.delay(session.id)
        caught.append(f'session {session.id} activation')

    for session in AcademicSession.objects.filter(status='Initiated', activation_deadline__isnull=False):
        if session.availability_deadline and session.availability_deadline <= now:
            session_availability_task.delay(session.id)
            caught.append(f'session {session.id} availability')

    for session in AcademicSession.objects.filter(status='Active', closing_deadline__isnull=False, closing_deadline__lte=now):
        session_closing_task.delay(session.id)
        caught.append(f'session {session.id} closing')

    if caught:
        logger.warning(
            'reconcile_lifecycle_states caught %s missed transition(s) — an eta-scheduled task '
            'was likely lost upstream: %s', len(caught), caught
        )
    else:
        logger.debug('reconcile_lifecycle_states: nothing to reconcile')

    return 'Lifecycle reconciliation sweep completed'


# Data Caching Tasks
@shared_task
def cache_faculty_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Faculty.objects.select_related(
        'employee_id', 'employee_id__user', 'employee_id__address', 'department'
    ).prefetch_related('employee_id__qualification_set')
    cache_key = 'admin:faculty_list'
    cache.delete(cache_key)
    serializer = FacultySerializer(queryset,context=context, many=True)
    cache.set(cache_key, serializer.data, timeout=60*10)

    designation_choices = Faculty.DESIGNATION_CHOICES
    departments = Department.objects.all()

    for each in departments:
        cache_key = f'admin:faculty:department:{each.department_id}'
        cache.delete(cache_key)
        dept_data = queryset.filter(department=each.department_id)
        serializer = FacultySerializer(dept_data, context=context,many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)
        for key, value in designation_choices:
            cache_key = f'admin:faculty:{each.department_id}:{key}'
            cache.delete(cache_key)
            data = dept_data.filter(designation=key)
            serializer = FacultySerializer(data,context=context, many=True)
            cache.set(cache_key, serializer.data, timeout=60*10)


    for key, value in designation_choices:
        cache_key = f'admin:faculty:designation:{key}'
        cache.delete(cache_key)
        designation_data = queryset.filter(designation=key)
        serializer = FacultySerializer(designation_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    return "Faculty data has been cached successfully"


@shared_task
def cache_student_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Student.objects.select_related(
        'student_id', 'student_id__user', 'student_id__address', 'program',
        'student_class', 'student_class__program',
    ).prefetch_related('student_id__qualification_set')
    cache_key = 'admin:student_list'
    cache.delete(cache_key)
    serializer = StudentSerializer(queryset,context=context, many=True)
    cache.set(cache_key, serializer.data, timeout=60*10)

    departments = Department.objects.all()
    programs = Program.objects.all()
    classes = Class.objects.all()
    status_choices = Student.STATUS_CHOICES

    for each in departments:
        cache_key = f'admin:students:department:{each.department_id}'
        cache.delete(cache_key)
        department_data = queryset.filter(program__department=each.department_id)
        serializer = StudentSerializer(department_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)
        for key, value in status_choices:
            cache_key = f'admin:students:{each.department_id}:{key}'
            cache.delete(cache_key)
            data = department_data.filter(status=key)
            serializer = StudentSerializer(data,context=context, many=True)
            cache.set(cache_key, serializer.data, timeout=60*10)


    for each in programs:
        cache_key = f'admin:students:program:{each.program_id}'
        cache.delete(cache_key)
        program_data = queryset.filter(program=each.program_id)
        serializer = StudentSerializer(program_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    for each in classes:
        cache_key = f'admin:students:class:{each.class_id}'
        cache.delete(cache_key)
        class_data = queryset.filter(student_class=each.class_id)
        serializer = StudentSerializer(class_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)



    for key, value in status_choices:
        cache_key = f'admin:students:status:{key}'
        cache.delete(cache_key)
        status_data = queryset.filter(status=key)
        serializer = StudentSerializer(status_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)

    return "Student data has been cached successfully"

@shared_task
def cache_programs_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Program.objects.all()
    cache_key = 'admin:programs_list'
    cache.delete(cache_key)
    serializer = ProgramSerializer(queryset,context=context, many=True)
    cache.set(cache_key, serializer.data, timeout=60*10)

    departments = Department.objects.all()
    for each in departments:
        cache_key = f'admin:programs:department:{each.department_id}'
        cache.delete(cache_key)
        dept_data = queryset.filter(department=each.department_id)
        serializer = ProgramSerializer(dept_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)

    return 'Program data has been cached successfully'


@shared_task
def cache_courses_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Course.objects.all()
    cache_key = 'admin:courses_list'
    cache.delete(cache_key)
    serializer = CourseSerializer(queryset, many=True, context=context)
    cache.set(cache_key, serializer.data, timeout=60*10)

    return "Course data has been cached successfully"


@shared_task
def cache_semester_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    logger.debug('cache_semester_data_task running for user_id=%s', user_id)
    context = {'request': custom_request}

    queryset = Semester.objects.all()
    cache_key = 'admin:semesters_list'
    cache.delete(cache_key)
    serializer = SemesterSerializer(queryset, many=True, context=context)
    cache.set(cache_key, serializer.data, timeout=60*10)

    classes = Class.objects.all()
    for each in classes:
        class_data = queryset.filter(associated_class=each.class_id)
        cache_key = f'admin:semesters:class:{each.class_id}'
        cache.delete(cache_key)
        serializer = SemesterSerializer(class_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    return "Semester data has been cached successfully"

@shared_task
def cache_courseAllocation_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = CourseAllocation.objects.all()
    semester_based_queryset = queryset.order_by('semester')
    semester_distributed_queryset = {
        semester_id : list(items) for semester_id, items in groupby(semester_based_queryset, key=lambda x : x.semester.semester_id)
    }
    for key, value in semester_distributed_queryset.items():
        cache_key = f'admin:allocations:semester:{key}'
        cache.delete(cache_key)
        serializer = CourseAllocationSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)

    faculty_based_queryset = queryset.order_by('faculty')
    faculty_distributed_queryset = {
        teacher_id : list(items) for teacher_id, items in groupby(faculty_based_queryset, key=lambda x : x.faculty)

    }
    for key, value in faculty_distributed_queryset.items():
        cache_key = f'admin:allocations:faculty:{key}'
        cache.delete(cache_key)
        serializer = CourseAllocationSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    return 'Course Allocation data has been cached successfully'


# EnrollmentSerializer reads obj.student.student_id (Student, then Person) and
# obj.result on every row. Without this the rebuild runs that lookup 75,000
# times.
def _enrollment_cache_queryset():
    return Enrollment.objects.select_related(
        'student__student_id', 'allocation__faculty', 'result'
    )


def _cache_enrollments_for(cache_key, enrollments, context):
    """Write one enrollment list to one key.

    No cache.delete() first: deleting leaves the key *absent* for the length of
    the rebuild, so every reader falls through to the database. Writing over it
    keeps the old value readable until the new one lands.

    An empty result is cached as [], not deleted. Empty is the true answer for
    a student with no enrollments left, and the view treats only None as a
    miss -- so deleting the key would make every later read miss and fire
    another rebuild.
    """
    serializer = EnrollmentSerializer(enrollments, context=context, many=True)
    cache.set(cache_key, serializer.data, timeout=60*10)


@shared_task
def cache_enrollment_data_task(user_id, student_ids=None, faculty_ids=None):
    """Rebuild the admin enrollment caches.

    With neither id list given this rebuilds every key -- all 75,000
    enrollments, serialised twice, into about 5,200 keys. That was the only
    behaviour, and it ran on every create, update and delete, so one student
    enrolling in one course rebuilt the cache for every student and every
    teacher in the system. Measured, the fill never finished inside 60
    seconds: it cost three CPU cores for minutes and returned nothing usable.

    One enrollment changing affects exactly two keys, so the write paths now
    name them. The full rebuild stays for callers that genuinely want it.

    `user_id` scopes nothing -- it only builds absolute URLs in the serializer.
    """
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = _enrollment_cache_queryset()

    if student_ids is None and faculty_ids is None:
        student_based_queryset = queryset.order_by('student')
        student_distributed_data = {
            student_id : list(items) for student_id, items in groupby(student_based_queryset, key=lambda x : x.student)
        }
        for key, value in student_distributed_data.items():
            _cache_enrollments_for(f'admin:enrollments:student:{key}', value, context)

        faculty_based_queryset = queryset.order_by('allocation__faculty')
        faculty_distributed_data = {
            teacher_id : list(items) for teacher_id, items in groupby(faculty_based_queryset, key=lambda x : x.allocation.faculty)
        }
        for key, value in faculty_distributed_data.items():
            _cache_enrollments_for(f'admin:enrollments:faculty:{key}', value, context)

        return 'Enrollment data has been cached successfully'

    # str(Student) and str(Faculty) both return person_id, which is also the
    # primary key the view reads out of the query param -- so the ids passed in
    # produce byte-identical key names to the full rebuild above.
    for student_id in dict.fromkeys(student_ids or ()):
        _cache_enrollments_for(
            f'admin:enrollments:student:{student_id}',
            queryset.filter(student=student_id),
            context,
        )

    for faculty_id in dict.fromkeys(faculty_ids or ()):
        _cache_enrollments_for(
            f'admin:enrollments:faculty:{faculty_id}',
            queryset.filter(allocation__faculty=faculty_id),
            context,
        )

    return 'Enrollment data has been cached successfully'

@shared_task
def cache_semester_enrollment_data_task(semester_id):
    semester = Semester.objects.filter(semester_id=semester_id).first()
    if not semester:
        return "Semester does not exist"

    class_id = semester.associated_class_id
    if not class_id:
        return "Semester has no associated class"

    cache_key = f'enrollments:{class_id}:semester:allocations'

    allocations = CourseAllocation.objects.filter(semester=semester).select_related('faculty__employee_id', 'course').all()
    data = []
    for each_allocation in allocations:
         data.append({'allocation_id':each_allocation.allocation_id,
                      'faculty_data':{'faculty_id' : each_allocation.faculty.employee_id.person_id,
                                               'faculty_name' : each_allocation.faculty.employee_id.first_name + ' ' + each_allocation.faculty.employee_id.last_name},
                      'course_data':{'course_code' : each_allocation.course.course_code,
                                               'course_name' : each_allocation.course.course_name,
                                               'credit_hours' : each_allocation.course.credit_hours,
                                                'lab': each_allocation.course.lab,}
                    })

    cache.set(cache_key, data,timeout=None)

    return 'Semester allocations data has been cached successfully'



@shared_task
def delete_faculty_task(person_id):
    faculty = Faculty.objects.filter(employee_id__person_id=person_id).first()
    if faculty:
        faculty.delete()
    return f'Faculty {person_id} deleted'


@shared_task
def delete_student_task(person_id):
    student = Student.objects.filter(student_id__person_id=person_id).first()
    if student:
        student.delete()
    return f'Student {person_id} deleted'


# Email Sending tasks
#
# These stay here as Celery tasks so their registered names never move — the
# message wording lives in email_service.py.
@shared_task
def send_hod_request_mail(request_id, confirmation_link):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    return email_service.send_hod_request(request, confirmation_link)


@shared_task
def send_hod_change_mail(request_id, old_hod_id):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    old_hod = Faculty.objects.filter(pk=old_hod_id).first() if old_hod_id else None
    return email_service.send_hod_appointment(request, old_hod)


@shared_task
def send_result_calculation_confirmation_mail(request_id):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    return email_service.send_result_calculation_approved(request)


@shared_task
def send_result_calculation_mail(request_id, confirmation_link, recipient_email):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    return email_service.send_result_calculation_request(
        request, confirmation_link, recipient_email,
    )
