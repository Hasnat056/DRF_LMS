import csv
import io
import re
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_list_or_404, get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from django.urls import reverse
from DjangoRESTProject_practice.celery import app

from Models.models import *
from django.contrib.auth.models import User
from FacultyModule.serializers import LectureSerializer, AssessmentSerializer
from StudentModule.serializers import ReviewsSerializer
from .mixins import PersonSerializerMixin, ResultCalculationMixin



class UserSerializer(serializers.ModelSerializer):
   class Meta:
       model = User
       fields = [
           'username',
           'password',
       ]
       extra_kwargs = {
           'username' : {'read_only': True},
           'password': {'write_only': True}
       }

class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            'country',
            'province',
            'city',
            'zipcode',
            'street_address',
        ]

class QualificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Qualification
        fields = [
            'degree_title',
            'education_board',
            'passing_year',
            'institution',
            'total_marks',
            'obtained_marks',
            'is_current'
        ]

    def validate_passing_year(self, value):
        if self.instance and self.instance.passing_year == value:
            return value
        if value is not None and int(value) > datetime.today().year:
            raise serializers.ValidationError("Passing year cannot be in the future")
        return value

    def validate(self, data):
        obtained_marks = data.get('obtained_marks')
        total_marks = data.get('total_marks')
        if obtained_marks and not total_marks:
            raise serializers.ValidationError("Total marks cannot be empty")
        if total_marks and not obtained_marks:
            raise serializers.ValidationError("Obtained marks cannot be empty")
        if obtained_marks and total_marks:
            if obtained_marks > total_marks:
                raise serializers.ValidationError("obtained_marks should be less than total_marks")

        return data


class PersonSerializer(serializers.ModelSerializer):
    address = AddressSerializer(required=False)
    qualification_set = QualificationSerializer(many=True, required=False)
    user = UserSerializer()
    class Meta:
        model = Person
        fields = [
            'user',
            'image',
            'person_id',
            'first_name',
            'last_name',
            'father_name',
            'gender',
            'dob',
            'cnic',
            'institutional_email',
            'personal_email',
            'contact_number',
            'religion',
            'address',
            'qualification_set',
        ]
        extra_kwargs = {
            'person_id': {'read_only': True},
        }


    def __init__(self,*args,**kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].context.update(self.context)
        self.fields['address'].context.update(self.context)
        self.fields['qualification_set'].context.update(self.context)

        # For PUT/PATCH requests instantiating the nested serializers with the proper model instances
        if hasattr(self.instance, 'address'):
            self.fields['address'].instance = self.instance.address
        if hasattr(self.instance, 'qualification_set'):
            self.fields['qualification_set'].instance = self.instance.qualification_set.all()

    def validate_contact_number(self, value):
        if self.instance and self.instance.contact_number == value:
            return value

        pattern = r'^\+?\d{10,14}$'
        if not re.match(pattern, value):
            raise serializers.ValidationError("Enter a valid contact number in format +923001234567")
        return value

    def validate_cnic(self, value):
        if self.instance and self.instance.cnic == value:
            return value
        if value:
            cleaned_cnic = re.sub('[^0-9]', '', value)
            if len(cleaned_cnic) != 13:
                raise serializers.ValidationError("CNIC must have 13 digits")

            cnic = f"{cleaned_cnic[:5]}-{cleaned_cnic[5:12]}-{cleaned_cnic[12]}"
            return cnic


    def validate_dob(self, value):
        if self.instance and self.instance.dob == value:
            return value
        if value is not None:
            age = datetime.today().year - value.year
            if value > datetime.today().date():
                raise serializers.ValidationError("Date of Birth cannot be in the future")
            if age < 14:
                raise serializers.ValidationError("Your age should be at least 14")
            if age > 80:
                raise serializers.ValidationError("Your age should be less than 80")
        return value



class FacultySerializer(PersonSerializerMixin, serializers.ModelSerializer):
    courseallocation_set = serializers.SerializerMethodField(read_only=True)
    person = PersonSerializer(source='employee_id')
    url = serializers.HyperlinkedIdentityField(
        view_name='Admin:faculty-detail',
        lookup_field='employee_id'
    )
    class Meta:
        model = Faculty
        fields = [
            'url',
            'person',
            'department_id',
            'designation',
            'joining_date',
            'courseallocation_set',
        ]

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Faculty):
            if self.context.get('request').user.groups.filter(name='Faculty').exists():
                extra_kwargs['department_id'] = {'read_only': True}
                extra_kwargs['designation'] = {'read_only': True}
                extra_kwargs['joining_date'] = {'read_only': True}
        return extra_kwargs


    def get_fields(self):
        fields = super().get_fields()
        # making fields of nested serializer; person = PersonSerializer(), read-only based on the user
        person = fields['person']
        if self.context.get('request') == 'PUT' or self.context.get('request') == 'PATCH' or isinstance(self.instance, Faculty):
            person.fields['user'].read_only = True
        if isinstance(self.instance,Faculty) and self.context.get('request').user.groups.filter(name='Faculty').exists():

            person.fields['person_id'].read_only = True
            person.fields['first_name'].read_only = True
            person.fields['last_name'].read_only = True
            person.fields['father_name'].read_only = True
            person.fields['cnic'].read_only = True
            person.fields['dob'].read_only = True
            person.fields['gender'].read_only = True
            person.fields['institutional_email'].read_only = True
            person.fields['user'].read_only = True
        return fields

    #used courseallocation as SerializerMethodField because CourseAllocationSerializer is defined below
    def get_courseallocation_set(self, obj):
        return CourseAllocationSerializer(
            obj.courseallocation_set.all(),
            many=True,
            context=self.context
        ).data


    def __init__ (self, *args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['person'].context.update(self.context)

        #For PUT/PATCH request instantiating the nested serializer;
        if isinstance(self.instance, Faculty):
            self.fields['person'].instance = self.instance.employee_id

        if not isinstance(self.instance, Faculty):
            self.fields.pop('courseallocation_set')

        if self.instance and self.context.get('request').user.groups.filter(name='Faculty').exists():
            self.fields.pop('url')
            self.fields.pop('courseallocation_set')


    @transaction.atomic
    def create(self, validated_data):
        return self.create_mixin(validated_data,'Faculty')

    @transaction.atomic
    def update(self, instance, validated_data):
        return self.update_mixin(instance,validated_data)


class StudentSerializer(PersonSerializerMixin, serializers.ModelSerializer):
    enrollment_set = serializers.SerializerMethodField()
    person = PersonSerializer(source='student_id')
    url = serializers.HyperlinkedIdentityField(
        view_name='Admin:student-detail',
        lookup_field='student_id'
    )
    class Meta:
        model = Student
        fields = [
            'url',
            'person',
            'program_id',
            'class_id',
            'admission_date',
            'status',
            'enrollment_set',
        ]

    def get_enrollment_set(self, obj):
        return EnrollmentSerializer(
            obj.enrollment_set.all(),
            many=True,
            context=self.context
        ).data

    def validate_admission_date(self, value):
        if self.instance and self.instance.admission_date == value:
            return value

        if value and value.year < datetime.today().year or value.year > datetime.today().year:
            raise serializers.ValidationError("Invalid admission date")
        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].context.update(self.context)

        if isinstance(self.instance, Student):
            self.fields['person'].instance = self.instance.student_id
        if not isinstance(self.instance, Student):
            self.fields.pop('enrollment_set')

        if self.instance and self.context.get('request').user.groups.filter(name='Student').exists():
            self.fields.pop('url')
            self.fields.pop('enrollment_set')

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Student) and self.context.get('request').user.groups.filter(name='Student').exists():
            extra_kwargs['program_id'] = {'read_only': True}
            extra_kwargs['class_id'] = {'read_only' : True}
            extra_kwargs['admission_date'] = {'read_only': True}
            extra_kwargs['status'] = {'read_only': True}
        return extra_kwargs

    def get_fields(self):
        fields = super().get_fields()
        person = fields['person']
        if self.instance and self.context.get('request').user.groups.filter(name='Student').exists():
            person.fields['first_name'].read_only = True
            person.fields['last_name'].read_only = True
            person.fields['father_name'].read_only = True
            person.fields['person_id'].read_only = True
            person.fields['institutional_email'].read_only = True
            person.fields['cnic'].read_only = True
            person.fields['gender'].read_only = True
            person.fields['dob'].read_only = True
            person.fields['user'].read_only = True
        return fields

    @transaction.atomic
    def create(self, validated_data):
       return self.create_mixin(validated_data,'Student')

    @transaction.atomic
    def update(self, instance, validated_data):
        return self.update_mixin(instance,validated_data)


class AdminSerializer(PersonSerializerMixin, serializers.ModelSerializer):
    person = PersonSerializer(source='employee_id')

    class Meta:
        model = Admin
        fields = [
            'person',
            'joining_date',
            'leaving_date',
            'marital_status',
            'office_location',
            'status'
        ]

    def get_extra_kwargs(self):
        request = self.context.get('request')
        extra_kwargs = super().get_extra_kwargs()
        if request and request.user.groups.filter(name='Admin').exists():
            extra_kwargs['joining_date'] = {'read_only': True}
            extra_kwargs['leaving_date'] = {'read_only': True}
            extra_kwargs['status'] = {'read_only': True}

        return extra_kwargs

    def get_fields(self):
        fields = super().get_fields()
        person = fields['person']
        if self.instance and self.context.get('request').user.groups.filter(name='Admin').exists():
            person.fields['first_name'].read_only = True
            person.fields['last_name'].read_only = True
            person.fields['father_name'].read_only = True
            person.fields['person_id'].read_only = True
            person.fields['institutional_email'].read_only = True
            person.fields['cnic'].read_only = True
            person.fields['gender'].read_only = True
            person.fields['dob'].read_only = True
            person.fields['user'].read_only = True
        return fields

    def __init__(self, *args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['person'].context.update(self.context)

        if isinstance(self.instance, Admin):
            self.fields['person'].instance = self.instance.employee_id


    @transaction.atomic
    def create(self, validated_data):
        return self.create_mixin(validated_data,'Admin')

    @transaction.atomic
    def update(self, instance, validated_data):
        return self.update_mixin(instance,validated_data)


class DepartmentSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name='Admin:department-detail',
        lookup_field='department_id'
    )
    class Meta:
        model = Department
        fields = '__all__'

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Department):
            extra_kwargs = {
                'department_name' :{'read_only': True},
                'department_inauguration_date' : {'read_only': True},
            }
        return extra_kwargs


    def update(self, instance, validated_data):
        faculty = Faculty.objects.get(employee_id=validated_data['HOD'])
        if instance.HOD == faculty:
            return instance

        request = ChangeRequest.objects.create(department=instance, new_hod=faculty,
                                               change_type='hod_change',
                                               requested_by=self.context.get('request').user)

        confirmation_link = self.context.get('request').build_absolute_uri(
            reverse('Admin:confirm-change-request', args=[request.confirmation_token])
        )
        from .tasks import send_hod_request_mail
        send_hod_request_mail.apply_async(args=[request.pk, confirmation_link], eta=(timezone.now() + timedelta(minutes=2)))

        return instance


class ProgramSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name='program-detail',
        lookup_field='program_id'
    )
    class Meta:
        model = Program
        fields = '__all__'

class CourseSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name = 'Admin:course-detail',
        lookup_field = 'course_code'
    )
    class Meta:
        model = Course
        fields = '__all__'

    def validate_credit_hours(self, value):
        if value < 0:
            raise serializers.ValidationError("Credit hours cannot be negative")
        if value > 5:
            raise serializers.ValidationError("Credit hours cannot be greater than 5")
        return value



    def create(self, validated_data):
        if validated_data['lab']:
            validated_data['credit_hours'] += 1

        course = Course.objects.create(**validated_data)
        return course

    def update(self, instance, validated_data):
        if instance.lab == True and validated_data['lab'] == False:
            validated_data['credit_hours'] -= 1

        if instance.lab == False and validated_data['lab'] == True:
            validated_data['credit_hours'] += 1

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance



class SemesterDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = SemesterDetails
        fields = [
            'course_code',
            'course_name',
            'class_id',
            'semester_id'
        ]
    def get_course_name(self, obj) -> str:
        if obj.course_code:
            return obj.course_code.course_name
        return None

class SemesterClassSerializer(serializers.ModelSerializer):

    urls = serializers.HyperlinkedIdentityField(
        view_name= 'Admin:semester-detail',
        lookup_field= 'semester_id'
    )
    semesterdetails_set = SemesterDetailSerializer(many=True)

    class Meta:
        model = Semester
        fields = [
            'urls',
            'semester_id',
            'semester_no',
            'session',
            'status',
            'semesterdetails_set',
        ]



    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['semesterdetails_set'].context.update(self.context)

        if hasattr(self.instance, 'semesterdetails_set'):
            for each in self.instance.semesterdetails_set.all():
                self.fields['semesterdetails_set'].instance = each



@extend_schema_field(SemesterClassSerializer(many=True))
class SchemeOfStudiesField(serializers.Field):
    def get_attribute(self, obj):
        return obj

    def to_representation(self, obj):
        semester_list = Semester.objects.filter(semesterdetails__class_id=obj.class_id).distinct().prefetch_related(
            'semesterdetails_set__course_code'
        )
        semester_serializer_list = []
        for each in semester_list:
            semester_serializer_list.append(SemesterClassSerializer(each, context=self.context).data)

        if semester_serializer_list:
            return semester_serializer_list
        return None

    def to_internal_value(self, data):
        return data



class ClassSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name='Admin:class-detail',
        lookup_field='class_id'
    )

    scheme_of_studies = SchemeOfStudiesField(source=None, required=False)
    class Meta:
        model = Class
        fields = [
            'urls',
            'class_id',
            'program_id',
            'batch_year',
            'scheme_of_studies',
        ]


    @transaction.atomic
    def create(self, validated_data):
        if 'scheme_of_studies' in validated_data:
            validated_data.pop('scheme_of_studies')
        new_class = Class.objects.create(**validated_data)
        numbers_of_semesters = Program.objects.filter(program_id=new_class.program_id).first().total_semesters

        created_semesters_list = []
        for i in range(numbers_of_semesters):
            semester = Semester.objects.create(semester_no=i+1)
            created_semesters_list.append(semester)

        initial_semesterdetails_list = []
        for each in created_semesters_list:
            semester_detail = SemesterDetails.objects.create(semester_id=each, class_id=new_class)
            initial_semesterdetails_list.append(semester_detail)

        return new_class

    @transaction.atomic
    def update(self, instance, validated_data):
        scheme_of_studies = validated_data.pop('scheme_of_studies')
        class_data = validated_data

        for attr, value in class_data.items():
            setattr(instance, attr, value)
            instance.save()

        semester_ids = [s['semester_id'] for s in scheme_of_studies]
        semester_queryset = get_list_or_404(Semester, semester_id__in=semester_ids)
        loaded_semesters = {each.semester_id: each for each in semester_queryset}

        for each_semester in scheme_of_studies:
            semester = loaded_semesters[each_semester['semester_id']]
            #print(semester)
            if semester:
                semester_detail_set = each_semester.pop('semesterdetails_set')
                if len(semester_detail_set) > 1 or (
                        len(semester_detail_set) == 1 and semester_detail_set[0]['course_code'] is not None):
                    SemesterDetails.objects.filter(semester_id=semester).delete()
                course_codes = [each['course_code'] for each in semester_detail_set if each['course_code'] is not None]


                if course_codes:
                    course_queryset = get_list_or_404(Course, course_code__in=course_codes)
                    loaded_course_codes = {each.course_code: each for each in course_queryset}
                    for each in semester_detail_set:
                        course = loaded_course_codes[each['course_code']]
                        SemesterDetails.objects.create(course_code=course, class_id=instance, semester_id=semester)

                if 'session' in each_semester:
                    semester.session = each_semester['session']
                semester.save()

            else:
                raise Http404(f"Semester with id {each_semester['semester_id']} not found")

        return instance


class EnrollmentSerializer(serializers.ModelSerializer):
    reviews = ReviewsSerializer(read_only=True)
    result = serializers.SerializerMethodField(read_only=True)
    urls = serializers.HyperlinkedIdentityField(
        view_name = 'Admin:enrollment-detail',
        lookup_field = 'enrollment_id'
    )
    student_info = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = Enrollment
        fields = [
            'urls',
            'enrollment_id',
            'student_id',
            'student_info',
            'allocation_id',
            'enrollment_date',
            'status',
            'result',
            'reviews',
        ]

    @extend_schema_field(
        inline_serializer(
            name='StudentData',
            fields={
                'student_id': serializers.CharField(),
                'name': serializers.CharField(),
            }
        )
    )
    def get_student_info(self, obj):
        if obj and hasattr(obj, 'student_id'):
            return {'student_id': obj.student_id.student_id.person_id,
                'name': obj.student_id.student_id.first_name + ' ' + obj.student_id.student_id.last_name}
        else:
            return None

    @extend_schema_field(
        inline_serializer(
            name='ResultData',
            fields={
                'result_id': serializers.IntegerField(),
                'obtained_marks': serializers.DecimalField(max_digits=10, decimal_places=2),
                'course_gpa': serializers.DecimalField(max_digits=10, decimal_places=2),
            }
        )
    )
    def get_result(self, obj):
        if hasattr(obj, 'result'):
            result_data = {'result_id': obj.result.result_id,
                        'obtained_marks': obj.result.obtained_marks, 'course_gpa': obj.result.course_gpa}

            return result_data
        return None

    def get_fields(self):
        fields = super().get_fields()
        queryset = CourseAllocation.objects.filter(status='Ongoing')
        if queryset.exists():
            fields['allocation_id'].queryset = queryset
        else:
            fields['allocation_id'].queryset = CourseAllocation.objects.none()


        request = self.context.get("request")
        if request and request.user.is_authenticated:
            if request.user.groups.filter(name="Faculty").exists():
                fields.pop("urls", None)
        return fields

    def create(self, validated_data):

        enrollment = Enrollment.objects.create(**validated_data)
        Result.objects.create(enrollment_id=enrollment)

        assessments = Assessment.objects.filter(allocation_id=enrollment.allocation_id)
        if assessments.exists():
            for each in assessments:
                AssessmentChecked.objects.create(enrollment_id=enrollment, assessment_id=each)

        return enrollment



class CourseAllocationSerializer(serializers.ModelSerializer, ResultCalculationMixin):
    enrollment_set = EnrollmentSerializer(many=True, read_only=True)
    lecture_set = LectureSerializer(many=True, read_only=True)
    assessment_set = AssessmentSerializer(many=True, read_only=True)
    urls = serializers.HyperlinkedIdentityField(
        view_name = 'Admin:allocation-detail',
        lookup_field= 'allocation_id'
    )

    class Meta:
        model = CourseAllocation
        fields = [
            'urls',
            'allocation_id',
            'teacher_id',
            'course_code',
            'semester_id',
            'session',
            'status',
            'file_upload',
            'assessment_set',
            'enrollment_set',
            'lecture_set',

        ]

    def get_fields(self):
        fields = super().get_fields()
        queryset = Semester.objects.filter(status='Inactive',session__isnull=False, activation_deadline__isnull=False)
        if queryset.exists():
            fields['semester_id'].queryset = queryset
        else:
            fields['semester_id'].queryset = Semester.objects.none()

        return fields

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        request = self.context.get("request")
        if request and (request.method == 'PUT' or request.method == 'PATCH') and request.user.groups.filter(name="Faculty").exists() and isinstance(self.instance, CourseAllocation):
            extra_kwargs = {
                'teacher_id':{'read_only': True},
                'course_code':{'read_only': True},
                'semester_id':{'read_only': True},
                'status':{'read_only': True},
                'session':{'read_only': True},
                'file_upload':{'read_only': True} if self.instance.status == 'Completed' else {'read_only': False},
            }
        if request and request.user.groups.filter(name="Admin").exists():
            extra_kwargs = {
                'file_upload':{'read_only': True},
                'session' : {'read_only': True},
                'status' : {'read_only': True},
            }

        return extra_kwargs

    def validate_file_upload(self, value):
        instance = getattr(self, 'instance', None)
        if value is None and (instance is None or instance.file_upload is None):
            return None

        if instance and value == instance.file_upload:
            return value

        if value is None and instance and instance.file_upload:
            return instance.file_upload

        allowed_extensions = ['jpeg', 'jpg', 'png', 'docx', 'pptx', 'zip', 'pdf', 'xlsx', 'csv']
        allowed_mime_types = [
            'image/jpeg', 'image/png',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # docx
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',  # pptx
            'application/zip',
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # xlsx
            'text/csv',
            'application/vnd.google-apps.spreadsheet'  # Google Sheet
        ]

        ext = value.name.split('.')[-1].lower()  # Get extension
        mime_type = getattr(value.file, 'content_type', None)

        if ext not in allowed_extensions and mime_type not in allowed_mime_types:
            raise serializers.ValidationError(
                "Invalid file type. Allowed formats are: jpeg, png, docx, pptx, zip, pdf, xlsx, csv, google sheet."
            )
        max_size = 50 * 1024 * 1024  # 50 MB
        if value.size > max_size:
            raise serializers.ValidationError("File size must not exceed 50 MB.")

        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not isinstance(self.instance, CourseAllocation):
            self.fields.pop('enrollment_set')
            self.fields.pop('lecture_set')
            self.fields.pop('assessment_set')



    def create(self, validated_data):
        semester = validated_data['semester_id']

        validated_data['session'] = semester.session
        course = validated_data['course_code']
        allowed_courses = Course.objects.filter(semesterdetails__semester_id=semester.semester_id)
        if not allowed_courses.exists():
            raise serializers.ValidationError(f"Semester: {semester} has no available courses")
        if course not in allowed_courses.all():
            raise serializers.ValidationError(f"Course: {course} is not allowed for the Semester: {semester}\n Available courses:\n"
           
                                               f"{", ".join(each.course_code for each in allowed_courses)}\n")

        already_allocation = semester.courseallocation_set.all().filter(course_code=course, teacher_id=validated_data['teacher_id'], semester_id=semester)
        if already_allocation.exists():
            raise serializers.ValidationError(f"Course allocation with these details already exist")
        allocation = CourseAllocation.objects.create(**validated_data)

        return allocation




class TranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcript
        fields = [
            'student_id',
            'semester_id',
            'total_credits',
            'semester_gpa'
        ]
        extra_kwargs = {
            'total_credits' : {'read_only': True},
            'semester_gpa' : {'read_only': True},
        }

    def validate(self, data):
        transcript = Transcript.objects.get(semester_id=data['semester_id'], student_id=data['student_id'])
        if transcript:
            raise serializers.ValidationError('Transcript already exists')
        return data

    def create(self, validated_data):
        student = Student.objects.get(student_id=validated_data['student_id'])
        semester = Semester.objects.get(semester_id=validated_data['semester_id'])

        if not student:
            raise serializers.ValidationError('Student not found')
        if not semester:
            raise serializers.ValidationError('Semester not found')


        semester_gpa = 0.00
        total_credits_attempted = 0.0
        enrollments = Enrollment.objects.filter(student_id=student, allocation_id__semester_id=semester).prefetch_related('result')
        if enrollments.exists() and [each.status=='Completed' for each in enrollments]:
            for each in enrollments:
                semester_gpa += each.result.course_gpa * each.allocation_id.course_code.credit_hours
                total_credits_attempted += each.allocation_id.course_code.credit_hours

            semester_gpa = semester_gpa/total_credits_attempted
            transcript = Transcript.objects.create(semester_id=semester, student_id=student, semester_gpa=semester_gpa, total_credits_attempted=total_credits_attempted)
            return transcript
        return None




class BulkTranscriptSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(write_only=True)
    class Meta:
        fields = [
            'confirm',
        ]


    def validate(self, data):
        if not data['confirm']:
            raise serializers.ValidationError('Confirmation required')
        return data

    @transaction.atomic
    def create(self, validated_data):
        if not validated_data['confirm']:
            return None

        data = []
        semester = Semester.objects.get(semester_id=self.context.get('semester_id'))

        # if semester is not found
        if not semester:
            raise serializers.ValidationError('Semester not found')

        if semester.status == 'Completed':
            raise serializers.ValidationError('Transcripts already exists')


        student_list = Student.objects.filter(enrollment__allocation_id__semester_id=semester).prefetch_related('enrollment_set').prefetch_related('enrollment_set__result')

        # checking if results exists for all enrollments of each student
        errors = {}
        for student in student_list:
            for enrollment in student.enrollment_set.all():
                if not enrollment.result or not enrollment.result.course_gpa:
                    errors[f'{student.student_id.person_id}'] = f'Result does not exist for enrollment {enrollment.enrollment_id}'

        if errors:
            raise serializers.ValidationError(errors)


        # semester_gpa and total_credits calculations using results for all enrollments of a student
        for each_student in student_list:
            gpa = 0.00
            total_credits_attempted = 0.0

            if each_student.enrollment_set.exists() and all([e.status == 'Completed' for e in each_student.enrollment_set.all()]):
                gpa += sum([e.result.course_gpa*e.allocation_id.course_code.credit_hours for e in each_student.enrollment_set.all()])
                total_credits_attempted += sum([e.allocation_id.course_code.credit_hours for e in each_student.enrollment_set.all()])

            gpa = gpa/total_credits_attempted

            data.append(
                Transcript(
                    student_id=each_student,
                    semester_id=semester,
                    total_credits=total_credits_attempted,
                    semester_gpa=gpa
                )
            )

        transcripts = Transcript.objects.bulk_create(data)

        return transcripts



class ChangeRequestSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name= 'Admin:change_request-detail',
        lookup_field= 'pk',
    )
    class Meta:
        model = ChangeRequest
        fields = '__all__'


    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, ChangeRequest):
           extra_kwargs = {
               'change_type': {'read_only': True},
               'department': {'read_only': True},
               'new_hod': {'read_only': True},
               'target_faculty': {'read_only': True},
               'target_student': {'read_only': True},
               'requested_at': {'read_only': True},
               'requested_by': {'read_only': True},
               'applied_at': {'read_only': True},
               'confirmation_token': {'read_only': True},
               'confirmed_at': {'read_only': True},
               'target_allocation' : {'read_only': True},
               'status' : {'read_only': True} if self.instance.status in ['applied', 'declined'] else {'read_only': False} ,
           }
        return extra_kwargs


    def update(self, instance, validated_data):
        if validated_data['status'] not in ['applied', 'declined']:
            return instance

        if validated_data['status'] == 'declined':
            instance.status = 'declined'
            instance.applied_at = timezone.now()
            instance.save()
            return instance

        if validated_data['status'] == 'applied':
            if instance.change_type == 'faculty_delete':
                if instance.target_faculty:
                    instance.target_faculty.delete()
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    return instance

            if instance.change_type == 'student_create':
                if instance.target_student:
                    instance.target_student.delete()
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    return instance

            if instance.change_type == 'hod_change':
                if instance.new_hod:
                    old_hod = instance.department.HOD if instance.department.HOD else None
                    department = get_object_or_404(Department, department_id=instance.department.department_id)
                    department.hod = instance.new_hod
                    department.save()
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    from .tasks import send_hod_change_mail
                    send_hod_change_mail.apply_aysnc(args=[instance.pk, old_hod], eta=timezone.now()+timedelta(minutes=2))
        return instance



class FacultyStudentBulkSerializer(serializers.Serializer):
    file = serializers.FileField()
    class Meta:
        fields = [
            'file'
        ]

    def validate(self, data):
        file = data['file']
        if not file.name.endswith('.csv') or file.name.endswith('.xlsx'):
            raise serializers.ValidationError('Invalid file type')

        if not file.content_type == 'text/csv' or file.content_type == 'application/vnd.ms-excel':
            raise serializers.ValidationError('Invalid file type')

        return data

    def create(self, validated_data):
        serializer_class = None
        insert_count = 0
        error_row_count = 0
        row_count = 0
        error_rows = []
        file = validated_data['file']
        #print(file)
        if file.name.endswith('.csv'):
            decoded_file = io.TextIOWrapper(file.file, encoding='utf-8-sig')
            file_data = csv.DictReader(decoded_file)

            if self.context.get('target_model')== 'faculty':
                serializer_class = FacultySerializer
            if self.context.get('target_model')== 'student':
                serializer_class = StudentSerializer
            else:
                return {'message': 'Provide a valid type'}

            for row in file_data:
                row_count += 1
                data = self.row_parser(row)
                serializer = serializer_class(data=data)
                if serializer.is_valid():
                    insert_count+=1
                    serializer.save()
                else:
                    error_row_count += 1
                    error_rows.append({ 'data_entry' : row,
                                        'errors' : serializer.errors})


        return {'row_count': row_count,'insert_count': insert_count, 'error_row_count': error_row_count, 'errors': error_rows}

    def row_parser(self,row):
        person_fields = ['image','first_name','last_name','father_name','gender','cnic','dob',
                  'contact_number','institutional_email','personal_email','religion']
        address_fields = ['country','province','city','zipcode','street_address']
        qualification_fields = ['degree_title','education_board','institution','passing_year',
                                'total_marks','obtained_marks','is_current']

        parsed_row = {}
        #parsing user data
        if 'password' in row:
            parsed_row = {'person' : {'user' : {'password': row['password']}}}

        #parsing person data fields
        for each_field in person_fields:
            if each_field in row:
                if row[each_field] == '':
                    parsed_row['person'][each_field] = None
                else:
                    parsed_row['person'][each_field] = row[each_field]

        #parsing address data
        address = {}
        for each_field in address_fields:
            if each_field in row:
                if row[each_field] == '':
                    address[each_field] = None
                else:
                    address[each_field] = row[each_field]
        parsed_row['person']['address'] = address #nesting address inside person

        #parsing faculty data if present
        if 'designation' in row:
            parsed_row['designation'] = row['designation']
        if 'department_id' in row:
            parsed_row['department_id'] = row['department_id']
            if 'joining_date' in row and row['joining_date'] != '':
                parsed_row['joining_date'] = row['joining_date']

        #parsing student_data if present
        if 'program_id' in row:
            parsed_row['program_id'] = row['program_id']
        if 'class_id' in row:
            parsed_row['class_id'] = row['class_id']
        if 'admission_date' in row and row['admission_date'] != '':
            parsed_row['admission_date'] = row['admission_date']


        #parsing qualification data
        qualifications = []
        for i in range(5):
            each_qualification = {}
            for each_field in qualification_fields:
                if f'{each_field}_{i+1}' in row and row[f'{each_field}_{i+1}'] != '':
                    each_qualification[each_field] = row[f'{each_field}_{i+1}']

            if each_qualification:
                qualifications.append(each_qualification)

        parsed_row['person']['qualification_set'] = qualifications


        return parsed_row


class SemesterSerializer(serializers.ModelSerializer):
    courseallocation_set = CourseAllocationSerializer(many=True, read_only=True)
    semesterdetails_set = SemesterDetailSerializer(many=True, read_only=True)
    transcript_set = TranscriptSerializer(many=True, read_only=True)
    associated_class = serializers.SerializerMethodField(read_only=True)
    transcript_generation_url = serializers.SerializerMethodField(read_only=True)
    url = serializers.HyperlinkedIdentityField(
        view_name='Admin:semester-detail',
        lookup_field='semester_id',
    )
    class Meta:
        model = Semester
        fields = [
            'url',
            'transcript_generation_url',
            'semester_id',
            'semester_no',
            'session',
            'status',
            'activation_deadline',
            'closing_deadline',
            'associated_class',
            'semesterdetails_set',
            'courseallocation_set',
            'transcript_set',

        ]
        extra_kwargs = {
            'semester_no': {'read_only': True},
            'status': {'read_only': True},
        }

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Semester):
            if not self.instance.activation_deadline:
                extra_kwargs['closing_deadline'] = {'read_only': True}
            if self.instance.activation_deadline  and self.instance.activation_deadline < timezone.now():
                extra_kwargs['activation_deadline'] = {'read_only': True}
                extra_kwargs['session'] = {'read_only': True}
            if self.instance.activation_deadline and self.instance.activation_deadline > timezone.now() and self.instance.status == 'Inactive':
                extra_kwargs['closing_deadline'] = {'read_only': True}
            if self.instance.activation_deadline and self.instance.closing_deadline and self.instance.closing_deadline < timezone.now():
                extra_kwargs['closing_deadline'] = {'read_only': True}
                extra_kwargs['activation_deadline'] = {'read_only': True}
                extra_kwargs['session'] = {'read_only': True}

        return extra_kwargs

    def validate_activation_deadline(self, value):
        if value < timezone.now():
            raise serializers.ValidationError('Activation deadline cannot be is the past')
        #if timezone.now() < value < timezone.now() + timedelta(days=7):
            #raise serializers.ValidationError('Set activation deadline at least a week ahead')
        return value

    def validate_closing_deadline(self, value):
        if value < timezone.now():
            raise serializers.ValidationError('Closing deadline cannot be is the past')
        #if timezone.now() < value < timezone.now() + timedelta(days=7):
            #raise serializers.ValidationError('Set closing deadline at least a week ahead')
        return value

    @extend_schema_field(OpenApiTypes.URI)
    def get_transcript_generation_url(self, obj):
        request = self.context.get("request")
        return request.build_absolute_uri(
            reverse("Admin:semester-transcripts-create", kwargs={"semester_id": obj.semester_id})
        )

    def get_associated_class(self, obj) -> str:
        linked_class = Class.objects.filter(semesterdetails__semester_id=obj.semester_id).distinct()
        if linked_class.exists():
            return str(linked_class.first())
        return None



    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)

        if not isinstance(self.instance, Semester):
            self.fields.pop('transcript_generation_url')
            self.fields.pop('courseallocation_set')
            self.fields.pop('transcript_set')
        if isinstance(self.instance, Semester):
            if not self.instance.closing_deadline:
                self.fields.pop('transcript_generation_url')

    def update(self, instance, validated_data):
        cache_key = f'semester:activation:{instance.semester_id}'
        if 'activation_deadline' in validated_data:
            associated_class = Class.objects.filter(semesterdetails__semester_id=instance.semester_id).first()
            if associated_class:
                active_semester = Semester.objects.filter(semesterdetails__class_id=associated_class, status='Active').first()
                if active_semester:
                    raise serializers.ValidationError(f"The Class: {associated_class} has already has an active semester going : {active_semester}")



            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            old_task_id = cache.get(f"semester:activation:{instance.semester_id}")
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(cache_key)

            from .tasks import semester_activation_task
            task = semester_activation_task.apply_async(args=[instance.semester_id], eta=instance.activation_deadline)
            cache.set(cache_key, task.id, timeout=None)

            return instance

        if 'closing_deadline' in validated_data:
            errors = {}
            for each in instance.courseallocation_set.all():
                for each_enrollment in each.enrollments.all():
                    if not each.result.course_gpa:
                        errors[each_enrollment.enrollment_id] = f'Course Allocation : {str(each)} has no result for enrollment {str(each_enrollment)}'


            if errors:
                raise serializers.ValidationError(errors)


            instance.closing_deadline = validated_data['closing_deadline']
            instance.save()

            old_task_id = cache.get(f"semester:closing:{instance.semester_id}")
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(cache_key)

            from .tasks import semester_closing_task
            task = semester_closing_task.apply_async(args=[instance.semester_id], eta=instance.closing_deadline)
            cache.set(cache_key, task.id, timeout=None)

            return instance

        return instance