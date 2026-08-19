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
        each.status = 'Ongoing'
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
    semester = Semester.objects.filter(semester_id=semester_id).prefetch_related(
        'courseallocation_set').prefetch_related('courseallocation_set__enrollment_set').first()
    if not semester:
        logger.warning('semester_closing_task fired for missing semester_id=%s', semester_id)
        return f'Semester {semester_id} has been closed successfully!'

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

    for semester in Semester.objects.filter(status='Inactive', activation_deadline__lte=now):
        semester_activation_task.delay(semester.semester_id)
        caught.append(f'semester {semester.semester_id} activation')

    for semester in Semester.objects.filter(status='Active', closing_deadline__isnull=False, closing_deadline__lte=now):
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
        'student_id', 'student_id__user', 'student_id__address', 'program'
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


@shared_task
def cache_enrollment_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Enrollment.objects.all()
    student_based_queryset = queryset.order_by('student')

    student_distributed_data = {
        student_id : list(items) for student_id, items in groupby(student_based_queryset, key=lambda x : x.student)
    }

    for key, value in student_distributed_data.items():
        cache_key = f'admin:enrollments:student:{key}'
        cache.delete(cache_key)
        serializer = EnrollmentSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    faculty_based_queryset = queryset.order_by('allocation__faculty')
    faculty_distributed_data = {

        teacher_id : list(items) for teacher_id, items in groupby(faculty_based_queryset, key=lambda x : x.allocation.faculty)
    }

    for key, value in faculty_distributed_data.items():
        cache_key = f'admin:enrollments:faculty:{key}'
        cache.delete(cache_key)
        serializer = EnrollmentSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60 *10)

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
@shared_task
def send_hod_request_mail(request_id, confirmation_link):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    faculty = request.new_hod

    send_mail(
        subject=f"HOD Change Request : {faculty.employee_id}",
        message=f"Dear {faculty.employee_id.first_name} {faculty.employee_id.last_name},\n"
                f"You have been requested to appoint as the new Head of Department for the {request.department.department_name}\n"
                f"If you are willing to uphold this responsibility, please confirm by clicking the link below:\n"
                f"Confirmation link : {confirmation_link} \n"
                f"The links will expire in 48 hours.\n"

                f"Thank you,\n"
                f"NAMAL UNIVERSITY, MAINWALI",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[faculty.employee_id.institutional_email],
    )
    return 'Email sent successfully'

@shared_task
def send_hod_change_mail(request_id, old_hod):
    request = get_object_or_404(ChangeRequest, pk=request_id)

    send_mail(
        subject=f"HOD Appointment : {request.new_hod}",
        message=f"Dear {request.new_hod.employee_id.first_name} {request.new_hod.employee_id.last_name},\n"
                f"Congratulations! You have been appointed as the new Head of Department for the {request.department.department_name}\n"
                f"Looking forward to your contributions for the welfare of the department\n"

                f"Thank you,\n"
                f"NAMAL UNIVERSITY, MAINWALI",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[request.new_hod.employee_id.institutional_email],
    )

    if old_hod is not None:
        send_mail(
            subject=f"HOD Change : {old_hod.employee_id}",
            message=f"Dear {old_hod.employee_id.first_name} {old_hod.employee_id.last_name},\n"
                    f"Your position as the Head of department for the {request.department.department_name} has been transferred to Mr. {request.department.HOD.employee_id.first_name} {request.department.HOD.employee_id.last_name}\n"
                    f"We thankyou for you services and contributions to the welfare of the department \n"
                    f"Thank you,\n"
                    f"NAMAL UNIVERSITY, MAINWALI",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[old_hod.employee_id.institutional_email],
        )

    return 'Emails sent successfully'


@shared_task
def send_result_calculation_confirmation_mail(request_id):
    request = get_object_or_404(ChangeRequest, pk=request_id)

    send_mail(
        subject=f"Result Calculation Request Approved",
        message=f"Dear Faculty member,\n"
                "Your request to calculate the result for the course allocation: \n"
                f"Course Allocation ID: {request.target_allocation.allocation_id}\n"
                f"Faculty ID: {request.target_allocation.faculty.employee_id.person_id}\n"
                f"Faculty Name: {request.target_allocation.faculty.employee_id.first_name} {request.target_allocation.faculty.employee_id.last_name}\n"
                f"Semester ID: {request.target_allocation.semester_id}\n"
                f"Session: {request.target_allocation.session}\n"
                f"has been approved by the admin. Kindly visit your portal to apply changes\n"


                f"Thank you,\n"
                f"NAMAL UNIVERSITY, MAINWALI",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[request.target_allocation.faculty.employee_id.institutional_email],
    )

    return 'Emails sent successfully'

@shared_task
def send_result_calculation_mail(request_id,confirmation_link, recipient_email):
    request = get_object_or_404(ChangeRequest, pk=request_id)
    allocation = request.target_allocation
    send_mail(
        subject=f"Result Calculation Request : {allocation.allocation_id}",
        message=f"Dear Admin,\n"
                "A result calculation request has been made for the course allocation: \n"
                f"Course Allocation ID: {allocation.allocation_id}\n"
                f"Faculty ID: {allocation.faculty.employee_id.person_id}\n"
                f"Faculty Name: {allocation.faculty.employee_id.first_name} {allocation.faculty.employee_id.last_name}\n"
                f"Semester ID: {allocation.semester.semester_id}\n"
                f"Session: {allocation.session}\n"
                f"To approve this request click the link below:\n"
                f"Confirmation link : {confirmation_link} \n"
                f"The links will expire in 48 hours.\n"

                f"Thank you,\n"
                f"NAMAL UNIVERSITY, MAINWALI",
        from_email=request.requested_by.username,
        recipient_list=[recipient_email],
    )
    return 'Emails sent successfully'