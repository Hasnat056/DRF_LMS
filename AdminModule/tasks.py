from itertools import groupby

from celery import shared_task

from Models.models import *
from django.core.cache import cache
from .serializers import FacultySerializer, StudentSerializer, ProgramSerializer, CourseSerializer, SemesterSerializer, \
    CourseAllocationSerializer, EnrollmentSerializer

from django.conf import settings
from django.http import QueryDict
from django.utils.encoding import iri_to_uri

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
def semester_activation_task(semester_id):
    semester = Semester.objects.filter(semester_id=semester_id).prefetch_related('courseallocation_set').prefetch_related('courseallocation_set__enrollment_set').first()
    if semester:
        if semester.status == 'Active':
            return "Semester already activated"

        semester.status = 'Active'
        semester.save()
        for each in semester.courseallocation_set.all():
            each.status = 'Ongoing'
            for enroll in each.enrollment_set.all():
                enroll.status = 'Active'
                enroll.save()
            each.save()

    return f'Semester {semester_id} has been activated successfully!'

@shared_task
def semester_closing_task(semester_id):
    semester = Semester.objects.filter(semester_id=semester_id).prefetch_related(
        'courseallocation_set').prefetch_related('courseallocation_set__enrollment_set').first()
    if semester:
        semester.status = 'Completed'
        semester.save()
        for each in semester.courseallocation_set.all():
            each.status = 'Completed'
            for enroll in each.enrollment_set.all():
                enroll.status = 'Completed'
                enroll.save()
            each.save()

    return f'Semester {semester_id} has been closed successfully!'



# Data Caching Tasks
@shared_task
def cache_faculty_data_task(user_id):
    user = User.objects.get(id=user_id)
    custom_request = CustomRequest(user, method='GET')
    context = {'request': custom_request}

    queryset = Faculty.objects.all()
    cache_key = 'admin:faculty_list'
    cache.delete(cache_key)
    serializer = FacultySerializer(queryset,context=context, many=True)
    cache.set(cache_key, serializer.data, timeout=60*10)

    designation_choices = Faculty.DESIGNATION_CHOICES
    departments = Department.objects.all()

    for each in departments:
        cache_key = f'admin:faculty:department:{each.department_id}'
        cache.delete(cache_key)
        dept_data = queryset.filter(department_id=each.department_id)
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

    queryset = Student.objects.all()
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
        department_data = queryset.filter(program_id__department_id=each.department_id)
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
        program_data = queryset.filter(program_id=each.program_id)
        serializer = StudentSerializer(program_data,context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    for each in classes:
        cache_key = f'admin:students:class:{each.class_id}'
        cache.delete(cache_key)
        class_data = queryset.filter(class_id=each.class_id)
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
        dept_data = queryset.filter(department_id=each.department_id)
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
    print(custom_request)
    context = {'request': custom_request}

    queryset = Semester.objects.all()
    cache_key = 'admin:semesters_list'
    cache.delete(cache_key)
    serializer = SemesterSerializer(queryset, many=True, context=context)
    cache.set(cache_key, serializer.data, timeout=60*10)

    classes = Class.objects.all()
    for each in classes:
        class_data = queryset.filter(semesterdetails__class_id=each.class_id).distinct()
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
    semester_based_queryset = queryset.order_by('semester_id')
    semester_distributed_queryset = {
        semester_id : list(items) for semester_id, items in groupby(semester_based_queryset, key=lambda x : x.semester_id)
    }
    for key, value in semester_distributed_queryset.items():
        cache_key = f'admin:allocations:semester:{key}'
        cache.delete(cache_key)
        serializer = CourseAllocationSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)

    faculty_based_queryset = queryset.order_by('teacher_id')
    faculty_distributed_queryset = {
        teacher_id : list(items) for teacher_id, items in groupby(faculty_based_queryset, key=lambda x : x.teacher_id)

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
    student_based_queryset = queryset.order_by('student_id')

    student_distributed_data = {
        student_id : list(items) for student_id, items in groupby(student_based_queryset, key=lambda x : x.student_id)
    }

    for key, value in student_distributed_data.items():
        cache_key = f'admin:enrollments:student:{key}'
        cache.delete(cache_key)
        serializer = EnrollmentSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60*10)


    faculty_based_queryset = queryset.order_by('allocation_id__teacher_id')
    faculty_distributed_data = {

        teacher_id : list(items) for teacher_id, items in groupby(faculty_based_queryset, key=lambda x : x.allocation_id.teacher_id)
    }

    for key, value in faculty_distributed_data.items():
        cache_key = f'admin:enrollments:faculty:{key}'
        cache.delete(cache_key)
        serializer = EnrollmentSerializer(value, context=context, many=True)
        cache.set(cache_key, serializer.data, timeout=60 *10)

    return 'Enrollment data has been cached successfully'
