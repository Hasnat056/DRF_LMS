"""
Stress-scale data for the endpoint performance audit.

`Models/management/commands/seed_demo_data.py` tops out at 10 faculty and 50
students — enough to click through the UI, nowhere near enough to make an N+1
visible. This builds a full three-session university instead: two closed
sessions with results and transcripts on record, and one live session mid-term
with marks being entered.

Everything goes in through `bulk_create`, the RNG is seeded, and the whole
thing is idempotent — `seed()` returns immediately if the marker session is
already present. Run it once into the reused test database (see
`benchmarks/conftest.py`) and every subsequent benchmark run starts warm.

Dial `SCALE` down while iterating on the harness; leave it at 1.0 for numbers
worth reporting.
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.db import transaction
from django.utils import timezone

from Models.models import (
    AcademicSession, Assessment, AssessmentChecked, Attendance, Class,
    Course, CourseAllocation, Department, Enrollment, Faculty, Lecture,
    Person, Program, Result, Semester, SemesterDetails, Student, Transcript,
    Admin as AdminModel,
)

SCALE = 1.0

DEPARTMENTS = 5
PROGRAMS = 10
FACULTY = int(200 * SCALE)
CLASSES = int(40 * SCALE)
STUDENTS = int(5000 * SCALE)
COURSES = 300

COURSES_PER_SEMESTER = 5          # -> 5 allocations per class-session
ASSESSMENTS_PER_ALLOCATION = 4

# Logins the benchmark authenticates as. Deliberately picked to be "heavy"
# accounts: the faculty holds allocations in all three sessions, the student
# has two completed sessions of history behind them.
ADMIN_EMAIL = 'bench.admin@stress.test'
FACULTY_EMAIL = 'bench.faculty@stress.test'
STUDENT_EMAIL = 'bench.student@stress.test'

# Presence of this session means the database is already seeded.
MARKER_PERIOD, MARKER_YEAR = 'Fall', 2099


def _log(out, msg):
    if out is not None:
        out.write(msg + '\n')
        out.flush()


def is_seeded():
    return AcademicSession.objects.filter(
        period=MARKER_PERIOD, year=MARKER_YEAR
    ).exists()


def seed(out=None):
    """Build the stress dataset. No-op if it is already there."""
    if is_seeded():
        _log(out, 'stress data already present — skipping seed')
        return
    # All or nothing: the marker session is created partway through, so a
    # failure after that point would otherwise leave a half-built dataset that
    # `is_seeded()` reports as complete.
    with transaction.atomic():
        _seed(out)


def _seed(out):
    rng = random.Random(20260905)
    now = timezone.now()

    # -- groups ------------------------------------------------------------
    groups = {
        name: Group.objects.get_or_create(name=name)[0]
        for name in ('Admin', 'Faculty', 'Student')
    }

    # -- departments / programs / classes ----------------------------------
    departments = Department.objects.bulk_create([
        Department(
            department_id=f'D{i:02d}',
            department_name=f'Department of Discipline {i}',
            department_inauguration_date=date(1990 + i, 1, 1),
        )
        for i in range(DEPARTMENTS)
    ])

    programs = Program.objects.bulk_create([
        Program(
            program_id=f'P{i:03d}',
            program_name=f'BS Discipline {i}',
            department=departments[i % DEPARTMENTS],
            total_semesters=8,
            fee_per_semester=150000,
        )
        for i in range(PROGRAMS)
    ])

    classes = Class.objects.bulk_create([
        Class(program=programs[i % PROGRAMS], batch_year=2019 + (i % 6))
        for i in range(CLASSES)
    ])
    classes = list(Class.objects.order_by('class_id'))
    _log(out, f'departments={len(departments)} programs={len(programs)} classes={len(classes)}')

    # -- courses -----------------------------------------------------------
    courses = Course.objects.bulk_create([
        Course(
            course_code=f'CRS-{i:04d}',
            course_name=f'Course Number {i}',
            credit_hours=rng.choice([1, 2, 3, 3, 3, 4]),
            lab=(i % 7 == 0),
            description=f'Auto-generated stress course {i}.',
        )
        for i in range(COURSES)
    ])
    _log(out, f'courses={len(courses)}')

    # -- people ------------------------------------------------------------
    # person_id is a 20-char PK and cnic/contact_number/email are all unique.
    # Every one is derived from `n`, which must therefore be unique across all
    # three roles, not just within one — hence the disjoint start offsets
    # below. (cnic encodes n as (n // 10000, n % 10000), which is injective.)
    def person_rows(prefix, kind, count, start):
        users, persons = [], []
        for i in range(count):
            n = start + i
            email = f'{prefix}{n}@stress.test'
            users.append(User(username=email, is_active=True))
            persons.append(Person(
                person_id=f'{prefix.upper()}-{n:06d}',
                first_name=f'{kind}{n}',
                last_name=f'Surname{n}',
                father_name=f'Father{n}',
                gender='Male' if n % 2 else 'Female',
                dob=date(1980 + (n % 25), 1 + (n % 12), 1 + (n % 28)),
                cnic=f'{35000 + (n // 10000):05d}-{n % 10000:04d}{n % 7}{n % 5}{n % 3}-{n % 9}',
                contact_number=f'+9230{n:08d}',
                institutional_email=email,
                type=kind,
            ))
        return users, persons

    fac_users, fac_persons = person_rows('fac', 'Faculty', FACULTY, 1)
    stu_users, stu_persons = person_rows('stu', 'Student', STUDENTS, 100_000)
    adm_users, adm_persons = person_rows('adm', 'Admin', 5, 900_000)

    User.objects.bulk_create(fac_users + stu_users + adm_users, batch_size=2000)
    by_username = {u.username: u for u in User.objects.filter(username__endswith='@stress.test')}
    for p in fac_persons + stu_persons + adm_persons:
        p.user = by_username[p.institutional_email]
    Person.objects.bulk_create(fac_persons + stu_persons + adm_persons, batch_size=2000)
    _log(out, f'people={len(fac_persons) + len(stu_persons) + len(adm_persons)}')

    faculty = Faculty.objects.bulk_create([
        Faculty(
            employee_id=p,
            department=departments[i % DEPARTMENTS],
            designation=rng.choice(['Professor', 'Associate Professor',
                                    'Assistant Professor', 'Lecturer']),
            joining_date=date(2015 + (i % 9), 1, 15),
        )
        for i, p in enumerate(fac_persons)
    ], batch_size=1000)

    students = Student.objects.bulk_create([
        Student(
            student_id=p,
            program=programs[i % PROGRAMS],
            student_class=classes[i % CLASSES],
            admission_date=date(2020 + (i % 5), 9, 1),
            status='Active',
        )
        for i, p in enumerate(stu_persons)
    ], batch_size=2000)

    AdminModel.objects.bulk_create([
        AdminModel(employee_id=p, office_location=f'Block {i}', status='Active')
        for i, p in enumerate(adm_persons)
    ])

    # Department heads — several admin views join through Department.HOD.
    for i, dept in enumerate(departments):
        dept.HOD = faculty[i]
    Department.objects.bulk_update(departments, ['HOD'])

    # Group membership is what gates the role-scoped endpoints (see CLAUDE.md).
    # `by_username` rather than the objects passed to bulk_create: MySQL does
    # not return generated primary keys, so those still carry id=None.
    through = User.groups.through
    through.objects.bulk_create(
        [through(user_id=by_username[u.username].id, group_id=groups[role].id)
         for role, batch in (('Faculty', fac_users),
                             ('Student', stu_users),
                             ('Admin', adm_users))
         for u in batch],
        batch_size=2000,
    )
    _log(out, f'faculty={len(faculty)} students={len(students)}')

    # -- benchmark logins --------------------------------------------------
    # Renaming three of the generated accounts is cheaper than special-casing
    # them through the bulk path above.
    bench_admin = adm_persons[0]
    bench_faculty = fac_persons[0]
    # A student in class 0, which the seeder gives a full three sessions of
    # history.
    bench_student = stu_persons[0]
    for person, email in ((bench_admin, ADMIN_EMAIL),
                          (bench_faculty, FACULTY_EMAIL),
                          (bench_student, STUDENT_EMAIL)):
        person.institutional_email = email
        person.user.username = email
        person.user.save(update_fields=['username'])
        person.save(update_fields=['institutional_email'])

    # -- sessions ----------------------------------------------------------
    # Past deadlines are recording history, not scheduling, so migration 0019's
    # minimum-window triggers do not apply to them; the live session's
    # deadlines sit inside the 2-4 week / 1-4 week windows the triggers do
    # enforce. `closing > activation` (migration 0018) holds for all three.
    sessions = [
        AcademicSession.objects.create(
            period='Fall', year=2024, status='Completed',
            activation_deadline=now - timedelta(days=400),
            closing_deadline=now - timedelta(days=280),
            availability_delta=7,
        ),
        AcademicSession.objects.create(
            period='Spring', year=2025, status='Completed',
            activation_deadline=now - timedelta(days=250),
            closing_deadline=now - timedelta(days=130),
            availability_delta=7,
        ),
        # The term in progress: activation is behind it, closing ahead. Only
        # one session may be live at a time (AdminModule/serializers.py:1409),
        # so there is no second session in the Initiated setup phase — the
        # bulk-worksheet benchmark stages that itself.
        AcademicSession.objects.create(
            period=MARKER_PERIOD, year=MARKER_YEAR, status='Active',
            activation_deadline=now - timedelta(days=60),
            closing_deadline=now + timedelta(days=20),
            availability_delta=7,
        ),
    ]
    live_session = sessions[-1]

    # -- semesters + scheme of studies -------------------------------------
    semesters = []
    for s_idx, session in enumerate(sessions):
        status = 'Completed' if session.status == 'Completed' else 'Active'
        for c_idx, klass in enumerate(classes):
            semesters.append(Semester(
                semester_no=1 + s_idx + (c_idx % 4),
                status=status,
                session=session,
                associated_class=klass,
            ))
    Semester.objects.bulk_create(semesters, batch_size=500)
    semesters = list(Semester.objects.filter(session__in=sessions).order_by('semester_id'))

    details = []
    semester_courses = {}
    for i, sem in enumerate(semesters):
        picked = [courses[(i * COURSES_PER_SEMESTER + k) % COURSES]
                  for k in range(COURSES_PER_SEMESTER)]
        semester_courses[sem.semester_id] = picked
        details += [SemesterDetails(course=c, semester=sem) for c in picked]
    SemesterDetails.objects.bulk_create(details, batch_size=1000)
    _log(out, f'semesters={len(semesters)} scheme_rows={len(details)}')

    # -- allocations -------------------------------------------------------
    allocations = []
    for i, sem in enumerate(semesters):
        for k, crs in enumerate(semester_courses[sem.semester_id]):
            # The benchmark faculty gets a normal teaching load — four
            # allocations per session, not one in all 120 semesters, which
            # would make their dashboard measure a load no real teacher has.
            mine = (k == 0 and i % CLASSES < 4)
            fac = faculty[0] if mine else faculty[(i * COURSES_PER_SEMESTER + k) % FACULTY]
            allocations.append(CourseAllocation(
                faculty=fac,
                course=crs,
                semester=sem,
                session=str(sem.session),
                status='Completed' if sem.status == 'Completed' else 'Active',
                passing_threshold=50,
            ))
    CourseAllocation.objects.bulk_create(allocations, batch_size=1000)
    allocations = list(
        CourseAllocation.objects.filter(semester__session__in=sessions)
        .select_related('semester')
        .order_by('allocation_id')
    )
    _log(out, f'allocations={len(allocations)}')

    # -- enrollments -------------------------------------------------------
    students_by_class = {}
    for st in students:
        students_by_class.setdefault(st.student_class_id, []).append(st)

    enrollments = []
    for alloc in allocations:
        cohort = students_by_class.get(alloc.semester.associated_class_id, ())
        status = 'Completed' if alloc.status == 'Completed' else 'Active'
        enrollments += [
            Enrollment(student=st, allocation=alloc, status=status)
            for st in cohort
        ]
    Enrollment.objects.bulk_create(enrollments, batch_size=5000)
    enrollments = list(
        Enrollment.objects.filter(allocation__semester__session__in=sessions)
        .order_by('enrollment_id')
    )
    _log(out, f'enrollments={len(enrollments)}')

    # -- assessments -------------------------------------------------------
    assessments = []
    for alloc in allocations:
        for k in range(ASSESSMENTS_PER_ALLOCATION):
            assessments.append(Assessment(
                allocation=alloc,
                assessment_type=['Quiz', 'Assignment', 'Midterm', 'Final'][k],
                assessment_name=f'{["Quiz", "Assign", "Mid", "Final"][k]} {k + 1}',
                weightage=[15, 15, 30, 40][k],
                assessment_date=date.today() - timedelta(days=60 - 10 * k),
                total_marks=[20, 50, 60, 100][k],
                student_submission=(k == 1),
                # The API refuses student_submission without a deadline
                # (FacultyModule/serializers.py:236), and StudentModule's
                # serializer compares this to now() unguarded, so leaving it
                # null seeds a state the app cannot actually produce.
                submission_deadline=(now + timedelta(days=7)) if k == 1 else None,
            ))
    Assessment.objects.bulk_create(assessments, batch_size=1000)
    assessments = list(
        Assessment.objects.filter(allocation__in=allocations).order_by('assessment_id')
    )

    by_allocation = {}
    for a in assessments:
        by_allocation.setdefault(a.allocation_id, []).append(a)

    # -- marks -------------------------------------------------------------
    # Completed sessions carry every assessment marked; the live session is
    # mid-term, so only the first two are in. That is what a real closing
    # deadline reminder would see.
    marks = []
    alloc_status = {a.allocation_id: a.status for a in allocations}
    for enr in enrollments:
        sheet = by_allocation.get(enr.allocation_id, ())
        n = len(sheet) if alloc_status[enr.allocation_id] == 'Completed' else 2
        for a in sheet[:n]:
            marks.append(AssessmentChecked(
                assessment=a,
                enrollment=enr,
                obtained=Decimal(rng.randint(int(a.total_marks * 0.35), a.total_marks)),
            ))
    AssessmentChecked.objects.bulk_create(marks, batch_size=10000)
    _log(out, f'assessments={len(assessments)} marks={len(marks)}')

    # -- results + transcripts for the closed sessions ---------------------
    closed_alloc_ids = {a.allocation_id for a in allocations if a.status == 'Completed'}
    results = [
        Result(
            enrollment=enr,
            course_gpa=Decimal(rng.choice(['4.00', '3.67', '3.33', '3.00',
                                           '2.67', '2.33', '2.00', '1.00', '0.00'])),
            obtained_marks=Decimal(rng.randint(35, 98)),
        )
        for enr in enrollments if enr.allocation_id in closed_alloc_ids
    ]
    Result.objects.bulk_create(results, batch_size=5000)

    closed_semesters = [s for s in semesters if s.status == 'Completed']
    transcripts = []
    for sem in closed_semesters:
        for st in students_by_class.get(sem.associated_class_id, ()):
            transcripts.append(Transcript(
                student=st,
                semester=sem,
                total_credits=rng.choice([15, 16, 17, 18]),
                semester_gpa=Decimal(f'{rng.uniform(2.0, 4.0):.2f}'),
            ))
    Transcript.objects.bulk_create(transcripts, batch_size=5000)
    _log(out, f'results={len(results)} transcripts={len(transcripts)}')

    # -- lectures + attendance for the benchmark faculty's live allocation --
    # Attendance endpoints are only interesting where lectures exist; seeding
    # every allocation would double the row count for no extra signal, so this
    # covers the accounts the benchmark actually logs in as.
    live_allocs = [a for a in allocations
                   if a.semester.session_id == live_session.id][:20]
    lectures = []
    for a in live_allocs:
        for n in range(1, 16):
            lectures.append(Lecture(
                lecture_id=f'L{a.allocation_id}-{n:02d}'[:10],
                allocation=a,
                lecture_no=n,
                venue=f'Room {n % 12}',
                starting_time=now - timedelta(days=60 - 3 * n),
                duration=90,
                topic=f'Topic {n}',
            ))
    Lecture.objects.bulk_create(lectures, batch_size=1000)
    lectures = list(Lecture.objects.filter(allocation__in=live_allocs))

    live_alloc_ids = {a.allocation_id for a in live_allocs}
    live_enrollments = [e for e in enrollments if e.allocation_id in live_alloc_ids]
    lectures_by_alloc = {}
    for lec in lectures:
        lectures_by_alloc.setdefault(lec.allocation_id, []).append(lec)

    attendance = []
    for enr in live_enrollments:
        for lec in lectures_by_alloc.get(enr.allocation_id, ()):
            attendance.append(Attendance(
                attendance_date=lec.starting_time.date(),
                enrollment=enr,
                lecture=lec,
                is_present=rng.random() > 0.15,
            ))
    Attendance.objects.bulk_create(attendance, batch_size=10000)
    _log(out, f'lectures={len(lectures)} attendance={len(attendance)}')
    _log(out, 'stress seed complete')
