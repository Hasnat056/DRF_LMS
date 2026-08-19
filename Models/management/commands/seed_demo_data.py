"""
Seeds the database with a realistic demo dataset: departments, programs,
courses, classes (with their auto-cascaded semesters/scheme of studies),
faculty, and students. Admin accounts are not seeded — they're login
credentials, not disposable demo data.

Idempotent — safe to run more than once. Existing rows (matched on their
natural key: department_id, program_id, course_code, or a person's cnic)
are left untouched; only missing ones are created.

Usage: python manage.py seed_demo_data
"""
import random
from datetime import date

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from Models.models import (
    Department, Program, Class, Course, Semester, SemesterDetails,
    Person, Address, Faculty, Student,
)
from AdminModule.mixins import PersonSerializerMixin

MALE_FIRST_NAMES = [
    'Ali', 'Ahmed', 'Hassan', 'Bilal', 'Usman', 'Hamza', 'Zain', 'Fahad',
    'Umer', 'Talha', 'Saad', 'Owais', 'Danish', 'Kamran', 'Waleed', 'Adeel',
    'Faisal', 'Imran', 'Rizwan', 'Shahzad', 'Asad', 'Junaid', 'Haris', 'Moiz',
]
FEMALE_FIRST_NAMES = [
    'Ayesha', 'Zainab', 'Fatima', 'Sana', 'Mahnoor', 'Hira', 'Iqra', 'Sadia',
    'Rabia', 'Amna', 'Mehak', 'Noor', 'Komal', 'Anum', 'Saba', 'Bushra',
    'Farah', 'Nida', 'Sidra', 'Laiba', 'Maria', 'Hafsa', 'Warda', 'Eman',
]
LAST_NAMES = [
    'Khan', 'Ahmed', 'Malik', 'Raza', 'Iqbal', 'Hussain', 'Farooq', 'Sheikh',
    'Chaudhry', 'Baig', 'Abbasi', 'Qureshi', 'Butt', 'Awan', 'Niazi', 'Tariq',
    'Javed', 'Aslam', 'Yousaf', 'Siddiqui', 'Mahmood', 'Rashid', 'Latif', 'Anwar',
]

CNIC_PREFIX = '61101'   # Mianwali district code — a fake, sequential block, not real people
PHONE_PREFIX = '+92300'


class NameGenerator:
    """Deterministic, collision-avoiding (first, last, gender) generator."""

    def __init__(self, seed=42):
        self._rng = random.Random(seed)
        self._used = set()

    def next(self):
        for _ in range(200):
            gender = self._rng.choice(['Male', 'Female'])
            pool = MALE_FIRST_NAMES if gender == 'Male' else FEMALE_FIRST_NAMES
            first = self._rng.choice(pool)
            last = self._rng.choice(LAST_NAMES)
            if (first, last) not in self._used:
                self._used.add((first, last))
                return first, last, gender
        # pool exhausted (shouldn't happen at our volumes) — allow repeats
        gender = self._rng.choice(['Male', 'Female'])
        pool = MALE_FIRST_NAMES if gender == 'Male' else FEMALE_FIRST_NAMES
        return self._rng.choice(pool), self._rng.choice(LAST_NAMES), gender


class Command(BaseCommand):
    help = 'Seeds realistic demo data: departments, programs, courses, classes, admin, faculty, students.'

    def handle(self, *args, **options):
        self._cnic_seq = 1
        self._phone_seq = 1
        self._names = NameGenerator()

        with transaction.atomic():
            departments = self.seed_departments()
            programs = self.seed_programs(departments)
            self.seed_courses(programs)
            classes = self.seed_classes(programs)
            self.seed_faculty(departments)
            self.seed_students(classes)

        self.stdout.write(self.style.SUCCESS('Demo data seeding complete.'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_cnic(self):
        cnic = f'{CNIC_PREFIX}-{self._cnic_seq:07d}-{self._cnic_seq % 10}'
        self._cnic_seq += 1
        return cnic

    def _next_phone(self):
        phone = f'{PHONE_PREFIX}{self._phone_seq:07d}'
        self._phone_seq += 1
        return phone

    def _person_payload(self, first, last, gender, dob, email_local, email_domain='nexus.edu.pk'):
        email = f'{email_local}@{email_domain}'
        return {
            'first_name': first,
            'last_name': last,
            'father_name': f'{random.choice(LAST_NAMES)} {last}',
            'gender': gender,
            'dob': dob,
            'cnic': self._next_cnic(),
            'contact_number': self._next_phone(),
            'institutional_email': email,
            'address': {
                'country': 'Pakistan',
                'province': random.choice(['Punjab', 'Khyber Pakhtunkhwa', 'Sindh']),
                'city': random.choice(['Mianwali', 'Lahore', 'Islamabad', 'Rawalpindi', 'Multan']),
                'zipcode': random.choice([54000, 42000, 44000, 46000, 60000]),
                'street_address': f'House {random.randint(1, 400)}, Street {random.randint(1, 40)}',
            },
            'user': {'password': 'ChangeMe123!'},
        }

    # ------------------------------------------------------------------
    # Departments
    # ------------------------------------------------------------------

    def seed_departments(self):
        specs = [
            ('DCS', 'Department of Computer Sciences', date(1990, 2, 15)),
            ('DES', 'Department of Engineering Sciences', date(1995, 1, 1)),
            ('DAPS', 'Department of Applied and Pure Sciences', date(2000, 9, 10)),
            ('DHHS', 'Department of Humanities and Humanitarian Sciences', date(2002, 2, 15)),
            ('DMS', 'Department of Management Sciences', date(2008, 6, 1)),
        ]
        departments = {}
        for dept_id, name, inaugurated in specs:
            dept, created = Department.objects.get_or_create(
                department_id=dept_id,
                defaults={'department_name': name, 'department_inauguration_date': inaugurated},
            )
            departments[dept_id] = dept
            if created:
                self.stdout.write(f'  + Department {dept_id}')
        return departments

    # ------------------------------------------------------------------
    # Programs
    # ------------------------------------------------------------------

    def seed_programs(self, departments):
        specs = [
            ('BSCS', 'Bachelors of Science in Computer Sciences', 'DCS', 8, 90000),
            ('BSSE', 'Bachelors of Science in Software Engineering', 'DCS', 8, 95000),
            ('BSAI', 'Bachelors of Science in Artificial Intelligence', 'DCS', 8, 100000),
            ('BSEE', 'Bachelors of Science in Electrical Engineering', 'DES', 8, 130000),
            ('BSCE', 'Bachelors of Science in Civil Engineering', 'DES', 8, 125000),
            ('BSMATH', 'Bachelors of Science in Mathematics', 'DAPS', 8, 70000),
            ('BSPHY', 'Bachelors of Science in Physics', 'DAPS', 8, 70000),
            ('BSENG', 'Bachelors of Science in English', 'DHHS', 8, 60000),
            ('BSECO', 'Bachelors of Science in Economics', 'DHHS', 8, 65000),
            ('BBA', 'Bachelors of Business Administration', 'DMS', 8, 85000),
        ]
        programs = {}
        for program_id, name, dept_id, total_semesters, fee in specs:
            program, created = Program.objects.get_or_create(
                program_id=program_id,
                defaults={
                    'program_name': name,
                    'department': departments[dept_id],
                    'total_semesters': total_semesters,
                    'fee_per_semester': fee,
                },
            )
            programs[program_id] = program
            if created:
                self.stdout.write(f'  + Program {program_id}')
        return programs

    # ------------------------------------------------------------------
    # Courses
    # ------------------------------------------------------------------

    def seed_courses(self, programs):
        specs = [
            ('CS-100', 'Programming Fundamentals', 3, True),
            ('CS-101', 'Object Oriented Programming', 3, True),
            ('CS-201', 'Data Structures and Algorithms', 3, True),
            ('CS-202', 'Database Systems', 3, True),
            ('CS-203', 'Operating Systems', 3, True),
            ('CS-204', 'Computer Networks', 3, False),
            ('CS-205', 'Discrete Structures', 3, False),
            ('CS-301', 'Software Engineering', 3, False),
            ('CS-302', 'Web Application Development', 3, True),
            ('CS-303', 'Artificial Intelligence', 3, True),
            ('CS-304', 'Machine Learning', 3, True),
            ('CS-305', 'Computer Architecture', 3, False),
            ('CS-306', 'Information Security', 3, False),
            ('CS-401', 'Compiler Construction', 3, True),
            ('CS-402', 'Distributed Systems', 3, False),
            ('CS-403', 'Cloud Computing', 3, True),
            ('EE-101', 'Circuit Analysis', 3, True),
            ('EE-102', 'Electronics I', 3, True),
            ('EE-201', 'Digital Logic Design', 3, True),
            ('EE-202', 'Signals and Systems', 3, False),
            ('EE-203', 'Electromagnetic Theory', 3, False),
            ('EE-301', 'Control Systems', 3, True),
            ('EE-302', 'Power Systems', 3, False),
            ('EE-303', 'Microprocessors', 3, True),
            ('EE-304', 'Renewable Energy Systems', 3, False),
            ('MATH-101', 'Calculus I', 3, False),
            ('MATH-102', 'Calculus II', 3, False),
            ('MATH-201', 'Linear Algebra', 3, False),
            ('MATH-202', 'Differential Equations', 3, False),
            ('MATH-203', 'Probability and Statistics', 3, False),
            ('MATH-301', 'Numerical Analysis', 3, True),
            ('ENG-101', 'English Composition', 3, False),
            ('ENG-102', 'Communication Skills', 3, False),
            ('ECO-101', 'Principles of Economics', 3, False),
            ('PSY-101', 'Introduction to Psychology', 3, False),
            ('BBA-101', 'Principles of Management', 3, False),
            ('BBA-102', 'Financial Accounting', 3, False),
            ('BBA-201', 'Marketing Management', 3, False),
            ('BBA-202', 'Business Statistics', 3, False),
            ('BBA-301', 'Human Resource Management', 3, False),
        ]
        courses = {}
        for code, name, credit_hours, lab in specs:
            course, created = Course.objects.get_or_create(
                course_code=code,
                defaults={'course_name': name, 'credit_hours': credit_hours, 'lab': lab},
            )
            courses[code] = course
            if created:
                self.stdout.write(f'  + Course {code}')

        # a couple of prerequisite chains, for realism
        self._set_prereq(courses, 'CS-101', 'CS-100')
        self._set_prereq(courses, 'CS-201', 'CS-101')
        self._set_prereq(courses, 'CS-301', 'CS-202')
        self._set_prereq(courses, 'EE-102', 'EE-101')
        self._set_prereq(courses, 'MATH-102', 'MATH-101')

        return courses

    def _set_prereq(self, courses, course_code, prereq_code):
        course = courses[course_code]
        if course.pre_requisite_id != prereq_code:
            course.pre_requisite = courses[prereq_code]
            course.save(update_fields=['pre_requisite'])

    # ------------------------------------------------------------------
    # Classes (+ auto-cascaded Semester / SemesterDetails)
    # ------------------------------------------------------------------

    def seed_classes(self, programs):
        specs = [
            ('BSCS', 2023),
            ('BSEE', 2023),
            ('BSMATH', 2024),
            ('BSSE', 2024),
            ('BBA', 2023),
        ]
        # course scheme (first two semesters) per program, for a populated,
        # realistic-looking scheme of studies instead of empty placeholders
        scheme = {
            'BSCS': [['CS-100', 'MATH-101', 'ENG-101'], ['CS-101', 'MATH-102', 'CS-205']],
            'BSEE': [['EE-101', 'MATH-101', 'ENG-101'], ['EE-102', 'MATH-102', 'EE-201']],
            'BSMATH': [['MATH-101', 'ENG-101'], ['MATH-102', 'MATH-201']],
            'BSSE': [['CS-100', 'MATH-101', 'ENG-101'], ['CS-101', 'CS-205', 'MATH-102']],
            'BBA': [['BBA-101', 'ENG-101', 'ECO-101'], ['BBA-102', 'BBA-201', 'PSY-101']],
        }

        classes = {}
        for program_id, batch_year in specs:
            program = programs[program_id]
            klass, created = Class.objects.get_or_create(program=program, batch_year=batch_year)
            classes[(program_id, batch_year)] = klass

            if not created:
                continue

            self.stdout.write(f'  + Class {klass.class_id} ({program_id}-{batch_year})')

            for i in range(program.total_semesters):
                semester = Semester.objects.create(semester_no=i + 1, associated_class=klass)
                course_codes = scheme.get(program_id, [])
                if i < len(course_codes):
                    for code in course_codes[i]:
                        SemesterDetails.objects.create(
                            semester=semester,
                            course=Course.objects.get(course_code=code),
                        )
                else:
                    SemesterDetails.objects.create(semester=semester)

        return classes

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------
    # Admin accounts aren't seeded here — they're login credentials, not
    # disposable demo data. See the cleanup step that made the existing
    # admin(s) realistic instead of creating more.

    def seed_faculty(self, departments):
        if Faculty.objects.count() >= 10:
            return

        designations = ['Lecturer', 'Senior Lecturer', 'Assistant Professor', 'Associate Professor', 'Professor']
        dept_ids = list(departments.keys())
        mixin = PersonSerializerMixin()

        needed = 10 - Faculty.objects.count()
        for i in range(needed):
            first, last, gender = self._names.next()
            dept_id = dept_ids[i % len(dept_ids)]
            department = departments[dept_id]
            person_data = self._person_payload(
                first, last, gender, date(1985 - (i % 10), (i % 12) + 1, (i % 27) + 1),
                f'{first.lower()}.{last.lower()}',
            )
            mixin.create_mixin(
                {
                    'employee_id': person_data,
                    'department': department,
                    'designation': random.choice(designations),
                    'joining_date': date(2015 + (i % 8), 1, 1),
                },
                'Faculty',
            )
            self.stdout.write(f'  + Faculty {first} {last} ({dept_id})')

    def seed_students(self, classes):
        if Student.objects.count() >= 50:
            return

        class_list = list(classes.values())
        needed = 50 - Student.objects.count()

        # create_mixin's Student branch derives the next sequence number from
        # Student.objects.filter(admission_date__year=<this year>).count() —
        # fine for real signups, but seeded students intentionally carry a
        # historical admission_date (their class's real batch year), so that
        # filter never matches them and every student in a program collides
        # on the same id. Use our own stable per-program counter instead.
        year = timezone.now().year
        counters = {}

        for i in range(needed):
            first, last, gender = self._names.next()
            klass = class_list[i % len(class_list)]
            program = klass.program
            admission_date = date(klass.batch_year, 9, 1)
            person_data = self._person_payload(
                first, last, gender, date(klass.batch_year - 19, (i % 12) + 1, (i % 27) + 1),
                f'{first.lower()}.{last.lower()}.{klass.batch_year}',
            )

            if program.program_id not in counters:
                counters[program.program_id] = Student.objects.filter(program=program).count()
            counters[program.program_id] += 1
            person_data['person_id'] = f'NUM-{program.program_id}-{year}-{counters[program.program_id]}'

            user_data = person_data.pop('user')
            address_data = person_data.pop('address')
            user = User.objects.create_user(**user_data, username=person_data['institutional_email'])
            person = Person.objects.create(**person_data, type='Student', user=user)
            Student.objects.create(
                student_id=person, program=program, student_class=klass,
                admission_date=admission_date, status='Active',
            )
            Address.objects.create(**address_data, person_id=person)
            user.groups.add(Group.objects.get(name='Student'))

            self.stdout.write(f'  + Student {first} {last} ({program.program_id}-{klass.batch_year})')
