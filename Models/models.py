import uuid

from django.db import models
from django.contrib.auth.models import User
from datetime import  datetime
from django.utils import timezone
from django.db.models import CheckConstraint, Q


def current_time():
    return timezone.now().date()

class Department(models.Model):
    department_id = models.AutoField(primary_key=True)
    department_name = models.CharField(max_length=100)
    department_inauguration_date = models.DateField(db_column='establishmentDate', blank=True, null=True)
    HOD = models.ForeignKey('Faculty', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table = 'department'

    def __str__(self):
        return self.department_name

class Program (models.Model):
    program_id = models.CharField(primary_key=True, max_length=10)
    program_name = models.CharField(max_length=100)
    department_id = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True)
    total_semesters = models.IntegerField(blank=True, default=8)
    fee_per_semester = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'program'

    def __str__(self):
        return self.program_id


class Class(models.Model):
    class_id = models.AutoField(primary_key=True)
    program_id = models.ForeignKey('Program', on_delete=models.SET_NULL, null=True)
    batch_year = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'class'
        ordering = ['batch_year']


    def __str__(self):
        return f"{self.program_id}-{self.batch_year}"



class Semester(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Completed', 'Completed'),
    ]
    semester_id = models.AutoField(primary_key=True)
    semester_no = models.IntegerField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='Inactive')
    session = models.CharField(max_length=15, blank=True, null=True)
    activation_deadline = models.DateTimeField(blank=True, null=True)
    closing_deadline = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'semester'

    def __str__(self):
        class_id = self.semesterdetails_set.values_list('class_id', flat=True).first()
        if class_id:
            class_object = Class.objects.get(class_id=class_id)
            class_name = f'{class_object.program_id.program_id} {class_object.batch_year}'
            return f"{class_name}-0{self.semester_no}-{self.session}"
        else:
            return f"None-0{self.semester_no}-{self.session}"




class Course(models.Model):
    course_code = models.CharField(primary_key=True, max_length=20)
    course_name = models.CharField(max_length=100)
    credit_hours = models.IntegerField()
    lab = models.BooleanField(default=False)
    pre_requisite = models.ForeignKey('self', on_delete=models.SET_NULL, db_column='preRequisite', blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'course'

    def __str__(self):
        return self.course_code




class SemesterDetails (models.Model):
    id = models.AutoField(primary_key=True)
    course_code = models.ForeignKey('Course', on_delete=models.CASCADE, blank=True, null=True)
    class_id = models.ForeignKey('Class', on_delete=models.RESTRICT)
    semester_id = models.ForeignKey('Semester', on_delete=models.RESTRICT)
    class Meta:
        db_table = 'semesterDetails'
        unique_together = (('course_code', 'class_id', 'semester_id'),)



class CourseAllocation(models.Model):

    STATUS_CHOICES = [
        ('Inactive', 'Inactive'),
        ('Ongoing','Ongoing'),
        ('Completed','Completed'),
        ('Cancelled','Cancelled'),
    ]
    allocation_id = models.AutoField(primary_key=True)
    teacher_id = models.ForeignKey('Faculty', on_delete=models.RESTRICT)
    course_code = models.ForeignKey('Course', on_delete=models.RESTRICT)
    semester_id = models.ForeignKey('Semester', on_delete=models.RESTRICT)
    session = models.CharField(max_length=20, blank=True, null=True)
    status = models.CharField(max_length=9, choices=STATUS_CHOICES, default='Inactive', db_column='status')

    class Meta:
        db_table = 'courseAllocation'

    def __str__(self):
        return f"[{self.course_code}_{self.teacher_id}_{self.session}]"

    def course_allocation_upload_path(instance, filename):
        # instance.allocation_id exists only after save, so fallback if None
        allocation_pk = instance.allocation_id or 'temp'
        return f'allocations/{allocation_pk}/uploads/{filename}'

    file_upload = models.FileField(
        upload_to=course_allocation_upload_path,
        blank=True, null=True
    )




class Assessment(models.Model):
    ASSESSMENT_TYPE_CHOICES = [
        ('Quiz', 'Quiz'),
        ('Assignment', 'Assignment'),
        ('Project', 'Project'),
        ('Presentation', 'Presentation'),
        ('Mid Exam', 'Mid Exam'),
        ('Final Exam', 'Final Exam'),
        ('Lab', 'Lab')
    ]
    assessment_id = models.AutoField( primary_key=True)
    allocation_id = models.ForeignKey('CourseAllocation', on_delete=models.CASCADE, db_column='allocationID')
    assessment_type = models.CharField(choices=ASSESSMENT_TYPE_CHOICES, max_length=15)
    assessment_name = models.CharField(max_length=20)
    weightage = models.IntegerField()
    assessment_date = models.DateField(blank=True, null=True)
    total_marks = models.IntegerField()
    student_submission = models.BooleanField(default=False)
    submission_deadline = models.DateTimeField(blank=True, null=True)


    class Meta:
        db_table = 'assessment'

    def __str__(self):
        return f"{self.allocation_id}--{self.assessment_name}"

    def assessment_upload_path(instance, filename):
        allocation_pk = instance.allocation_id.allocation_id if instance.allocation_id else 'temp'
        assessment_pk = instance.assessment_id or 'temp'
        return f'allocations/{allocation_pk}/{assessment_pk}/uploads/{filename}'

    file_upload = models.FileField(
        upload_to=assessment_upload_path,
        blank=True, null=True
    )


class AssessmentChecked(models.Model):
    id = models.AutoField(primary_key=True)
    assessment_id = models.ForeignKey('Assessment', on_delete=models.CASCADE)
    enrollment_id = models.ForeignKey('Enrollment', on_delete=models.CASCADE)
    obtained = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    class Meta:
        db_table = 'assessmentChecked'
        unique_together = (('assessment_id', 'enrollment_id'),)

    def assessment_checked_upload_path(instance, filename):
        assessment_pk = instance.assessment_id.assessment_id if instance.assessment_id else 'temp_assessment'
        allocation_pk = instance.assessment_id.allocation_id.allocation_id if instance.assessment_id and instance.assessment_id.allocation_id else 'temp_allocation'
        enrollment_pk = instance.enrollment_id.enrollment_id if instance.enrollment_id else 'temp_enrollment'
        return f'allocations/{allocation_pk}/{assessment_pk}/{enrollment_pk}/uploads/{filename}'

    student_upload = models.FileField(
        upload_to=assessment_checked_upload_path,
        blank=True, null=True
    )

class Lecture(models.Model):
    lecture_id = models.CharField(primary_key=True, max_length=10)
    allocation_id = models.ForeignKey('CourseAllocation', on_delete=models.CASCADE)
    lecture_no = models.IntegerField()
    venue = models.CharField(max_length=50, blank=True, null=True)
    starting_time = models.DateTimeField(db_column='startingTime')
    duration = models.IntegerField(blank=True, null=True)
    topic = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'lecture'
        ordering = ('starting_time',)


    def save(self, *args, **kwargs):
        if not self.lecture_id and self.allocation_id and self.lecture_no:
            self.lecture_id = f'{self.allocation_id}-{self.lecture_no}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lecture_id}"


class Attendance(models.Model):
    id = models.AutoField(primary_key=True)
    attendance_date = models.DateField(blank=True, null=True)
    student_id = models.ForeignKey('Student', on_delete=models.CASCADE)
    lecture_id = models.ForeignKey('Lecture', on_delete=models.CASCADE)
    is_present = models.BooleanField(default=False)

    class Meta:
        db_table = 'attendance'
        unique_together = (('attendance_date', 'student_id','lecture_id'),)

class Enrollment(models.Model):
    STATUS_CHOICES = [
        ('Inactive', 'Inactive'),
        ('Active', 'Active'),
        ('Completed', 'Completed'),
        ('Dropped', 'Dropped'),
    ]
    enrollment_id = models.AutoField(primary_key=True)
    student_id = models.ForeignKey('Student', on_delete=models.CASCADE)
    allocation_id = models.ForeignKey('CourseAllocation', on_delete=models.CASCADE)
    enrollment_date = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=9, default='Inactive', choices=STATUS_CHOICES)

    class Meta:
        db_table = 'enrollment'
        unique_together = (('student_id', 'allocation_id'),)

    def __str__(self):
        return f"{self.student_id}--{self.allocation_id}"


class Reviews(models.Model):
    review_id = models.AutoField(primary_key=True)
    enrollment_id = models.ForeignKey('Enrollment', on_delete=models.CASCADE)
    review_text = models.TextField(blank=True, null=True)
    rating = models.DecimalField(max_digits=4, decimal_places=2)
    create_date = models.DateTimeField(db_column='createdAt', default=datetime.now)

    class Meta:
        db_table = 'reviews'
        constraints = [
            CheckConstraint(
            check=Q(rating__gte=0.00) & Q(rating__lte=10.00),
            name= 'rating_range'
            )
        ]

        unique_together = (('review_id', 'enrollment_id'),)



class Result(models.Model):
    result_id = models.AutoField(primary_key=True)
    enrollment_id = models.OneToOneField('Enrollment', on_delete=models.CASCADE)
    course_gpa = models.DecimalField(max_digits=4, decimal_places=2, blank=True, null=True)
    obtained_marks = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)

    class Meta:
        db_table = 'result'
        constraints = [
            CheckConstraint(
                check=Q(obtained_marks__gte=0) & Q(obtained_marks__lte=100),
                name='valid_obtained_marks_range'
            ),
            CheckConstraint(
                check = Q(course_gpa__gte=0.00) & Q(course_gpa__lte=4.00),
                name='valid_course_gpa_range'
            )
        ]
        unique_together = (('enrollment_id', 'result_id'),)

    def __str__(self):
        return f"{self.enrollment_id}"




class Transcript(models.Model):
    id = models.AutoField(primary_key=True)
    student_id = models.ForeignKey('Student', on_delete=models.CASCADE)
    semester_id = models.ForeignKey('Semester', on_delete=models.CASCADE)
    total_credits = models.IntegerField()
    semester_gpa = models.DecimalField( max_digits=4, decimal_places=2)

    class Meta:
        db_table = 'transcript'
        unique_together = (('student_id', 'semester_id'),)
        constraints = [
            CheckConstraint(
                check = Q(semester_gpa__gte=0.00) & Q(semester_gpa__lte=4.00),
                name='semester_gpa_range'
            )
        ]



class Person(models.Model):
    GENDER_CHOICES = [
        ('Male', 'Male'),
        ('Female', 'Female'),
        ('Others', 'Others')
    ]

    TYPE_CHOICES = [
        ('Admin', 'Admin'),
        ('Faculty', 'Faculty'),
        ('Student', 'Student'),
    ]
    image = models.ImageField(upload_to="user_images/" ,null=True, blank=True)
    person_id = models.CharField(max_length=20, primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    father_name = models.CharField(max_length=100)
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES)
    dob = models.DateField(db_column='DOB')
    cnic = models.CharField(db_column='CNIC', max_length=15, unique=True)
    contact_number = models.CharField(max_length=15, unique=True)
    religion = models.CharField( max_length=100, blank=True, null=True)
    institutional_email = models.EmailField( unique=True)
    personal_email = models.EmailField( blank=True, null=True)
    user = models.OneToOneField(User, db_column='userID', on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(db_column='type', max_length=7, choices=TYPE_CHOICES)

    class Meta:
        db_table = 'person'

    def __str__(self):
        return self.person_id


class Admin(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Suspended', 'Suspended'),
        ('Acting', 'Acting'),
    ]

    MARITAL_STATUS_CHOICES = [
        ('Single', 'Single'),
        ('Married', 'Married'),
        ('Divorced', 'Divorced'),
        ('Widowed', 'Widowed'),
    ]
    employee_id = models.OneToOneField(Person, on_delete=models.RESTRICT, primary_key=True)
    joining_date = models.DateField(blank=True, default=timezone.now)
    leaving_date = models.DateField(blank=True, null=True)
    office_location = models.CharField( max_length=100, blank=True, null=True)
    marital_status = models.CharField(choices=MARITAL_STATUS_CHOICES, max_length=10, blank=True, null=True)
    status = models.CharField( max_length=10, choices=STATUS_CHOICES, default='Active')

    class Meta:
        db_table = 'Admin'

    def __str__(self):
        return self.employee_id_id


class Faculty(models.Model):


    DESIGNATION_CHOICES = [
        ('Lab Engineer', 'Lab Engineer'),
        ('Lecturer', 'Lecturer'),
        ('Senior Lecturer', 'Senior Lecturer'),
        ('Associate Professor', 'Associate Professor'),
        ('Assistant Professor', 'Assistant Professor'),
        ('Professor', 'Professor'),

    ]
    employee_id = models.OneToOneField(Person, on_delete=models.RESTRICT, primary_key=True)
    department_id = models.ForeignKey('Department',  on_delete=models.RESTRICT)
    designation = models.CharField(choices=DESIGNATION_CHOICES, max_length=20)
    joining_date = models.DateField(blank=True, default=current_time)

    class Meta:
        db_table = 'Faculty'

    def __str__(self):
        return self.employee_id.person_id

    def delete(self, using = None, keep_parents = False):
        person = self.employee_id
        super().delete(using=using, keep_parents=keep_parents)
        person.delete()



class Student(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Dropped', 'Dropped'),
        ('Frozen', 'Frozen'),
        ('Graduated', 'Graduated'),
        ('On Probation', 'On Probation'),
    ]

    student_id = models.ForeignKey('Person',  on_delete=models.RESTRICT)
    program_id = models.ForeignKey('Program',  on_delete=models.RESTRICT)
    class_id = models.ForeignKey('Class',  on_delete=models.RESTRICT)
    admission_date = models.DateField(blank=True, default=current_time)
    status = models.CharField(db_column='status', choices=STATUS_CHOICES, max_length=12, default='Active')

    class Meta:
        db_table = 'Student'

    def __str__(self):
        return f'{self.student_id.person_id}'

    def delete(self, using=None, keep_parents=False):
        person = self.student_id
        super().delete(using=using, keep_parents=keep_parents)
        person.delete()




class Address(models.Model):
    person_id = models.OneToOneField('Person', on_delete=models.CASCADE, primary_key=True)
    country = models.CharField(max_length=50, blank=True)
    province = models.CharField(max_length=50, blank=True, null=True)
    city = models.CharField(max_length=50, blank=True)
    zipcode = models.IntegerField(blank=True, null=True)
    street_address = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = 'address'



class Qualification(models.Model):
    qualification_id = models.AutoField(primary_key=True)
    person_id = models.ForeignKey('Person', on_delete=models.CASCADE)
    degree_title = models.CharField(max_length=50)
    education_board = models.CharField(max_length=20, blank=True, null=True)
    institution = models.CharField(max_length=50)
    passing_year = models.TextField(blank=True, null=True)
    total_marks = models.IntegerField(blank=True, null=True)
    obtained_marks = models.IntegerField(blank=True, null=True)
    is_current = models.IntegerField( blank=True, null=True)

    class Meta:
        db_table = 'qualification'



class AuditTrail(models.Model):
    audit_id = models.AutoField( primary_key=True)
    userid = models.ForeignKey('Person', on_delete=models.CASCADE,)
    action_type = models.CharField( max_length=6)
    entity_name = models.CharField( max_length=50)
    time_stamp = models.DateTimeField(default=datetime.now)
    ip_address = models.CharField( max_length=45)
    user_agent = models.CharField(max_length=255)
    old_value = models.JSONField(blank=True, null=True)
    new_value = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = 'auditTrail'




class ChangeRequest(models.Model):
    CHANGE_TYPES = [
        ('hod_change', 'HOD Change'),
        ('faculty_delete', 'Faculty Delete'),
        ('student_delete', 'Student Delete'),
        ('result_calculation', 'Result Calculation'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('declined', 'Declined'),
        ('applied', 'Applied'),
        ('expired', 'Expired'),
    ]

    # Core fields
    change_type = models.CharField(max_length=20, choices=CHANGE_TYPES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    confirmation_token = models.UUIDField(default=uuid.uuid4, unique=True)

    # For HOD changes
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True)
    new_hod = models.ForeignKey(Faculty, on_delete=models.CASCADE, null=True, blank=True)


    # For Result Calculations
    target_allocation = models.ForeignKey('CourseAllocation', on_delete=models.CASCADE, null=True, blank=True)
    # For deletions
    target_faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='deletion_requests')
    target_student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True,
                                       related_name='deletion_requests')

    # Tracking
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    requested_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'change_request'
