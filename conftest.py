import pytest
from unittest.mock import patch
from django.contrib.auth.models import User, Group
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
from datetime import date, timedelta
from Models.models import (
    Person, Department, Program, Class, Course,
    Faculty, Student, Admin, Semester, SemesterDetails,
    CourseAllocation, Enrollment, Result,
    Assessment, AssessmentChecked, Lecture, Attendance,
    ChangeRequest, AcademicSession,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_token(user):
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


def auth_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {get_token(user)}')
    return client


# ---------------------------------------------------------------------------
# Cloudinary — every FileField/ImageField in this project (Person.image,
# AssessmentChecked.student_upload, AllocationFile.file, etc.) uses
# MediaCloudinaryStorage by default, which uploads to the real Cloudinary
# API on save(). Tests shouldn't depend on a real third-party service being
# reachable (or on whichever account's credentials happen to be configured
# locally) — mock the actual network call so the whole suite runs offline.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_cloudinary_upload():
    with patch('cloudinary.uploader.upload') as mocked:
        mocked.return_value = {
            'public_id': 'test/mock-upload',
            'secure_url': 'https://res.cloudinary.com/test/mock-upload',
            'url': 'https://res.cloudinary.com/test/mock-upload',
            'resource_type': 'raw',
            'format': '',
        }
        yield mocked


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_group(db):
    group, _ = Group.objects.get_or_create(name='Admin')
    return group


@pytest.fixture
def faculty_group(db):
    group, _ = Group.objects.get_or_create(name='Faculty')
    return group


@pytest.fixture
def student_group(db):
    group, _ = Group.objects.get_or_create(name='Student')
    return group


# ---------------------------------------------------------------------------
# Persons & Users
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_person(db):
    user = User.objects.create_user(
        username='admin@test.com',
        password='adminpass123',
    )
    return Person.objects.create(
        person_id='NUM-ADM-2024-1',
        first_name='Admin',
        last_name='User',
        father_name='Admin Father',
        gender='Male',
        dob=date(1985, 1, 1),
        cnic='12345-1234567-1',
        contact_number='+923001234567',
        institutional_email='admin@test.com',
        type='Admin',
        user=user,
    )


@pytest.fixture
def faculty_person(db):
    user = User.objects.create_user(
        username='faculty@test.com',
        password='facultypass123',
    )
    return Person.objects.create(
        person_id='NUM-CS-2024-1',
        first_name='Faculty',
        last_name='User',
        father_name='Faculty Father',
        gender='Male',
        dob=date(1990, 1, 1),
        cnic='12345-1234567-2',
        contact_number='+923001234568',
        institutional_email='faculty@test.com',
        type='Faculty',
        user=user,
    )


@pytest.fixture
def student_person(db):
    user = User.objects.create_user(
        username='student@test.com',
        password='studentpass123',
    )
    return Person.objects.create(
        person_id='NUM-BSCS-2024-1',
        first_name='Student',
        last_name='User',
        father_name='Student Father',
        gender='Male',
        dob=date(2000, 1, 1),
        cnic='12345-1234567-3',
        contact_number='+923001234569',
        institutional_email='student@test.com',
        type='Student',
        user=user,
    )


# ---------------------------------------------------------------------------
# Base data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def department(db):
    return Department.objects.create(
        department_id='CS',
        department_name='Computer Science',
        department_inauguration_date=date(2010, 1, 1),
    )


@pytest.fixture
def program(db, department):
    return Program.objects.create(
        program_id='BSCS',
        program_name='BS Computer Science',
        department_id=department,
        total_semesters=8,
    )


@pytest.fixture
def batch_class(db, program):
    return Class.objects.create(
        program=program,
        batch_year=2022,
    )


@pytest.fixture
def course(db):
    return Course.objects.create(
        course_code='CS-101',
        course_name='Intro to Programming',
        credit_hours=3,
        lab=False,
    )


# ---------------------------------------------------------------------------
# Role model instances
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_instance(db, admin_person, admin_group):
    admin_person.user.groups.add(admin_group)
    return Admin.objects.create(
        employee_id=admin_person,
        joining_date=date(2020, 1, 1),
        status='Active',
    )


@pytest.fixture
def faculty_instance(db, faculty_person, faculty_group, department):
    faculty_person.user.groups.add(faculty_group)
    return Faculty.objects.create(
        employee_id=faculty_person,
        department=department,
        designation='Lecturer',
        joining_date=date(2021, 1, 1),
    )


@pytest.fixture
def student_instance(db, student_person, student_group, program, batch_class):
    student_person.user.groups.add(student_group)
    return Student.objects.create(
        student_id=student_person,
        program=program,
        student_class=batch_class,
        admission_date=date(2022, 1, 1),
        status='Active',
    )


# ---------------------------------------------------------------------------
# Semester fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def academic_session(db):
    return AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')


@pytest.fixture
def inactive_semester(db, batch_class, course, academic_session):
    semester = Semester.objects.create(
        semester_no=1,
        status='Inactive',
        session=academic_session,
        associated_class=batch_class,
    )
    SemesterDetails.objects.create(
        semester=semester,
        course=course,
    )
    return semester


@pytest.fixture
def second_faculty(db, faculty_group, department):
    """A second teacher, for testing reassignment."""
    user = User.objects.create_user(username='faculty2@test.com', password='facultypass123')
    user.groups.add(faculty_group)
    person = Person.objects.create(
        person_id='NUM-CS-2024-2',
        first_name='Second', last_name='Teacher', father_name='F',
        gender='Female', dob=date(1988, 5, 1),
        cnic='12345-1234567-9', contact_number='+923001234599',
        institutional_email='faculty2@test.com', type='Faculty', user=user,
    )
    return Faculty.objects.create(
        employee_id=person, department=department,
        designation='Lecturer', joining_date=date(2021, 1, 1),
    )


@pytest.fixture
def previous_session(db):
    """A separate, already-running session. A class runs one semester per
    session (unique_together on Semester), so an Active semester for the same
    class cannot share `academic_session` with `inactive_semester`."""
    return AcademicSession.objects.create(period='Spring', year=2024, status='Active')


@pytest.fixture
def active_semester(db, batch_class, course, previous_session):
    semester = Semester.objects.create(
        semester_no=2,
        status='Active',
        session=previous_session,
        associated_class=batch_class,
    )
    SemesterDetails.objects.create(
        semester=semester,
        course=course,
    )
    return semester


@pytest.fixture
def course_allocation(db, faculty_instance, course, inactive_semester):
    return CourseAllocation.objects.create(
        faculty=faculty_instance,
        course=course,
        semester=inactive_semester,
        session='Fall-2024',
        status='Inactive',
    )


@pytest.fixture
def enrollment(db, student_instance, course_allocation):
    enrollment = Enrollment.objects.create(
        student=student_instance,
        allocation=course_allocation,
        status='Inactive',
    )
    Result.objects.create(enrollment=enrollment)
    return enrollment


# ---------------------------------------------------------------------------
# Authenticated API clients
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(admin_instance):
    return admin_instance.employee_id.user


@pytest.fixture
def admin_client(admin_instance):
    return auth_client(admin_instance.employee_id.user)


@pytest.fixture
def faculty_client(faculty_instance):
    return auth_client(faculty_instance.employee_id.user)


@pytest.fixture
def student_user(student_instance):
    return student_instance.student_id.user


@pytest.fixture
def student_client(student_instance):
    return auth_client(student_instance.student_id.user)


@pytest.fixture
def anon_client():
    return APIClient()


# ---------------------------------------------------------------------------
# Celery eager execution
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def celery_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


# ---------------------------------------------------------------------------
# Faculty-specific fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def faculty_user(faculty_instance):
    return faculty_instance.employee_id.user


@pytest.fixture
def assessment(db, course_allocation):
    return Assessment.objects.create(
        allocation=course_allocation,
        assessment_type='Quiz',
        assessment_name='Quiz 1',
        assessment_date=date.today(),
        weightage=10,
        total_marks=20,
        student_submission=False,
    )


@pytest.fixture
def assessment_checked(db, assessment, enrollment):
    return AssessmentChecked.objects.create(
        assessment=assessment,
        enrollment=enrollment,
        obtained=None,
    )


@pytest.fixture
def lecture(db, course_allocation, enrollment):
    lec = Lecture.objects.create(
        lecture_id=f'{course_allocation.allocation_id}-1',
        lecture_no=1,
        allocation=course_allocation,
        starting_time=timezone.now() - timedelta(hours=1),
        venue='Room 101',
        duration=60,
        topic='Introduction',
    )
    Attendance.objects.create(
        lecture=lec,
        enrollment=enrollment,
        attendance_date=lec.starting_time.date(),
    )
    return lec


@pytest.fixture
def change_request(db, faculty_instance, course_allocation):
    return ChangeRequest.objects.create(
        change_type='result_calculation',
        target_allocation=course_allocation,
        requested_by=faculty_instance.employee_id.user,
        status='pending',
        requested_at=timezone.now(),
    )