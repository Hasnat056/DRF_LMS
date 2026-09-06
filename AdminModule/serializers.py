import csv
import io
import logging
import re
from datetime import timedelta, datetime
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch, RestrictedError
from django.http import Http404
from django.shortcuts import get_list_or_404, get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers
from django.urls import reverse
from NexusAPI.celery import app

from Models.models import *
from django.contrib.auth.models import User
from FacultyModule.serializers import LectureSerializer, AssessmentSerializer
from StudentModule.serializers import ReviewsSerializer
from .mixins import PersonSerializerMixin, ResultCalculationMixin, TranscriptGenerationMixin

logger = logging.getLogger(__name__)

# A lab is a course in its own right, coded and named after the theory course
# it belongs to. Admins never type either one -- ticking the lab box on a
# course derives both. The code stays terse where it is read in bulk; the
# name spells it out where it is read on its own.
LAB_COURSE_CODE_SUFFIX = '-L'
LAB_COURSE_NAME_SUFFIX = '-Lab'
COURSE_CODE_MAX_LENGTH = Course._meta.get_field('course_code').max_length
COURSE_NAME_MAX_LENGTH = Course._meta.get_field('course_name').max_length


def _lab_course_name(course_name):
    return f'{course_name}{LAB_COURSE_NAME_SUFFIX}'[:COURSE_NAME_MAX_LENGTH]



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
            today = datetime.today().date()
            age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
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
            'department',
            'designation',
            'joining_date',
            'courseallocation_set',
        ]

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Faculty):
            if self.context.get('request').user.groups.filter(name='Faculty').exists():
                extra_kwargs['department'] = {'read_only': True}
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
    student_class_display = serializers.StringRelatedField(source='student_class', read_only=True)
    url = serializers.HyperlinkedIdentityField(
        view_name='Admin:student-detail',
        lookup_field='student_id'
    )
    class Meta:
        model = Student
        fields = [
            'url',
            'person',
            'program',
            'student_class',
            'student_class_display',
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

        if value and (value.year < datetime.today().year or value.year > datetime.today().year):
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
            extra_kwargs['program'] = {'read_only': True}
            extra_kwargs['student_class'] = {'read_only' : True}
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
        hod_data = validated_data.pop('HOD', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if hod_data is None:
            return instance

        faculty = Faculty.objects.get(employee_id=hod_data)
        if instance.HOD and instance.HOD == faculty:
            return instance

        request = ChangeRequest.objects.create(department=instance, new_hod=faculty,
                                               change_type='hod_change',
                                               requested_by=self.context.get('request').user)

        confirmation_link = self.context.get('request').build_absolute_uri(
            reverse('Admin:confirm-change-request', args=[request.confirmation_token])
        )
        from .tasks import send_hod_request_mail
        send_hod_request_mail.apply_async(args=[request.pk, confirmation_link], eta=(timezone.now() + timedelta(minutes=2)))

        if faculty.employee_id.user_id:
            Notification.objects.create(
                recipient=faculty.employee_id.user,
                verb='hod_nomination',
                message=f'You have been nominated as Head of Department for {instance.department_name}.',
                level='action_required',
                content_type=ContentType.objects.get_for_model(ChangeRequest),
                object_id=request.pk,
            )

        return instance


class ProgramSerializer(serializers.ModelSerializer):
    urls = serializers.HyperlinkedIdentityField(
        view_name='Admin:program-detail',
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
    # The admin UI is a checkbox, so `lab` stays a boolean on the wire even
    # though it is a relation underneath: ticking it builds the lab course,
    # clearing it takes the lab course away. course_code is the primary key,
    # so lab_id is already the lab's code -- reading through it keeps both
    # fields join-free.
    lab = serializers.BooleanField(source='lab_id', required=False)
    lab_course = serializers.CharField(source='lab_id', read_only=True)

    class Meta:
        model = Course
        fields = '__all__'

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # DRF turns a null source into None before the field ever sees it, so
        # a course without a lab would report `lab: null` rather than false.
        data['lab'] = instance.lab_id is not None
        return data

    def validate_credit_hours(self, value):
        if value < 0:
            raise serializers.ValidationError("Credit hours cannot be negative")
        if value > 5:
            raise serializers.ValidationError("Credit hours cannot be greater than 5")
        return value

    def _build_lab(self, course):
        """Point the course at its {code}-L lab, creating it if it is missing.

        Nothing stops an admin adding a lab as a plain course first, so a row
        already sitting at the derived code is adopted rather than rejected.
        Refusing would strand the pair: the course cannot be saved with the
        box ticked, and saving it without leaves both rows in place with no
        link and no way to make one.
        """
        lab_code = f'{course.course_code}{LAB_COURSE_CODE_SUFFIX}'
        if len(lab_code) > COURSE_CODE_MAX_LENGTH:
            raise serializers.ValidationError({
                'lab': f"Course code '{course.course_code}' leaves no room for a "
                       f"'{LAB_COURSE_CODE_SUFFIX}' suffix within "
                       f"{COURSE_CODE_MAX_LENGTH} characters"
            })

        lab = Course.objects.filter(course_code=lab_code).first()
        if lab is None:
            lab = Course.objects.create(
                course_code=lab_code,
                course_name=_lab_course_name(course.course_name),
                credit_hours=1,
            )
        else:
            if Course.objects.filter(lab=lab).exclude(pk=course.pk).exists():
                raise serializers.ValidationError({
                    'lab': f"Course '{lab_code}' is already the lab of another course"
                })
            # Adopted, so bring the derived half into line -- the name follows
            # the theory course everywhere else too. Its credit hours stay as
            # the admin entered them; nothing else recomputes those.
            lab.course_name = _lab_course_name(course.course_name)
            lab.save(update_fields=['course_name'])

        course.lab = lab
        course.save(update_fields=['lab'])

    def _drop_lab(self, course):
        """Clearing the checkbox deletes the lab course, if it is free to go.

        Nothing special guards it -- what holds for a course holds for its
        lab, so an allocated lab is protected by the same RESTRICT that
        protects any allocated course. That surfaces as a 400 rather than the
        500 an uncaught RestrictedError would produce.
        """
        lab = course.lab
        course.lab = None
        course.save(update_fields=['lab'])
        try:
            lab.delete()
        except RestrictedError:
            raise serializers.ValidationError({
                'lab': f"Course '{lab.course_code}' is allocated and cannot be removed"
            })

    @transaction.atomic
    def create(self, validated_data):
        has_lab = validated_data.pop('lab_id', False)
        course = Course.objects.create(**validated_data)
        if has_lab:
            self._build_lab(course)
        return course

    @transaction.atomic
    def update(self, instance, validated_data):
        has_lab = validated_data.pop('lab_id', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if has_lab is True and instance.lab_id is None:
            self._build_lab(instance)
        elif has_lab is False and instance.lab_id is not None:
            self._drop_lab(instance)

        # The lab's name is derived from the theory course's, so a rename has
        # to carry across. course_code is the primary key and cannot change.
        if instance.lab_id is not None and 'course_name' in validated_data:
            lab = instance.lab
            lab.course_name = _lab_course_name(instance.course_name)
            lab.save(update_fields=['course_name'])

        return instance



class SemesterDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = SemesterDetails
        fields = [
            'course',
            'course_name',
            'semester'
        ]
    def get_course_name(self, obj) -> str:
        if obj.course:
            return obj.course.course_name
        return 'None'


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
            self.fields['semesterdetails_set'].instance = self.instance.semesterdetails_set.all()



@extend_schema_field(SemesterClassSerializer(many=True))
class SchemeOfStudiesField(serializers.Field):
    def get_attribute(self, obj):
        return obj

    def to_representation(self, obj):
        # Read the reverse relation instead of building a fresh queryset. The
        # old `Semester.objects.filter(associated_class=obj.class_id)` ran once
        # per class and could not see any prefetch the view had already done --
        # 3 queries per row, 30 of the 34 on a page of ten.
        #
        # Both views that use ClassSerializer prefetch semester_set with its
        # details and courses. A caller that does not will still get correct
        # output (the reverse manager is scoped by the FK), just one query per
        # class and an N+1 on semesterdetails_set.
        semester_list = obj.semester_set.all()
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
            'program',
            'batch_year',
            'scheme_of_studies',
        ]


    @transaction.atomic
    def create(self, validated_data):
        if 'scheme_of_studies' in validated_data:
            validated_data.pop('scheme_of_studies')
        new_class = Class.objects.create(**validated_data)
        numbers_of_semesters = Program.objects.filter(program_id=new_class.program.program_id).first().total_semesters

        created_semesters_list = []
        for i in range(numbers_of_semesters):
            semester = Semester.objects.create(semester_no=i+1, associated_class=new_class)
            created_semesters_list.append(semester)

        initial_semesterdetails_list = []
        for each in created_semesters_list:
            semester_detail = SemesterDetails.objects.create(semester=each)
            initial_semesterdetails_list.append(semester_detail)

        return new_class

    @transaction.atomic
    def update(self, instance, validated_data):
        scheme_of_studies = validated_data.pop('scheme_of_studies', [])
        class_data = validated_data

        for attr, value in class_data.items():
            setattr(instance, attr, value)
            instance.save()

        if not scheme_of_studies:
            return instance

        logger.debug('Updating scheme_of_studies=%s', scheme_of_studies)

        touched_sessions = set()
        semester_ids = [s['semester_id'] for s in scheme_of_studies]
        semester_queryset = get_list_or_404(Semester, semester_id__in=semester_ids)
        loaded_semesters = {each.semester_id: each for each in semester_queryset}

        for each_semester in scheme_of_studies:
            semester = loaded_semesters[each_semester['semester_id']]
            if semester:
                semester_detail_set = each_semester.pop('semesterdetails_set')
                if len(semester_detail_set) > 1 or (
                        len(semester_detail_set) == 1 and semester_detail_set[0]['course'] is not None):
                    SemesterDetails.objects.filter(semester=semester).delete()
                course_codes = [each['course'] for each in semester_detail_set if each['course'] is not None]


                if course_codes:
                    course_queryset = get_list_or_404(
                        Course.objects.select_related('lab'), course_code__in=course_codes
                    )
                    # A course and its lab are offered side by side, so putting
                    # the theory course in the scheme puts its lab there too.
                    # Only the structure follows: allocation and enrolment stay
                    # separate, which is what lets a student repeat a lab alone.
                    scheduled = {each.course_code: each for each in course_queryset}
                    for course in list(scheduled.values()):
                        if course.lab_id and course.lab_id not in scheduled:
                            scheduled[course.lab_id] = course.lab
                    for course in scheduled.values():
                        SemesterDetails.objects.create(course=course, semester=semester)

                if 'session' in each_semester and each_semester['session'] is not None:
                    # A class runs one semester per session — activation
                    # cascades from the session, so a second binding would put
                    # two semesters of this class live at once. The DB enforces
                    # this too; checking here turns a 500 into a clean 400.
                    clash = (
                        Semester.objects
                        .filter(associated_class=instance, session_id=each_semester['session'])
                        .exclude(semester_id=semester.semester_id)
                        .first()
                    )
                    if clash:
                        logger.warning(
                            'Rejected session binding for semester_id=%s: class %s already has '
                            'semester_id=%s on session_id=%s',
                            semester.semester_id, instance, clash.semester_id, each_semester['session']
                        )
                        raise serializers.ValidationError(
                            f'Class {instance} already has semester {clash.semester_no} bound to '
                            f'this session. A class runs one semester per session.'
                        )

                if 'session' in each_semester:
                    semester.session_id = each_semester['session']
                semester.save()
                touched_sessions.add(semester.session_id)

            else:
                raise Http404(f"Semester with id {each_semester['semester_id']} not found")

        # The bulk allocation worksheet caches this structure — courses and
        # session bindings both come from here, so a scheme-of-studies edit
        # makes it stale.
        for session_id in touched_sessions:
            if session_id:
                cache.delete(f'admin:{session_id}:allocations:bulk')

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
            'student',
            'student_info',
            'allocation',
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
        if obj and hasattr(obj, 'student'):
            return {'student_id': obj.student.student_id.person_id,
                'student_name': obj.student.student_id.first_name + ' ' + obj.student.student_id.last_name}
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

        if not self.context.get('request'):
            fields['allocation'].queryset = CourseAllocation.objects.none()
            return fields


        queryset = CourseAllocation.objects.filter(status='Active')
        if queryset.exists():
            fields['allocation'].queryset = queryset
        else:
            fields['allocation'].queryset = CourseAllocation.objects.none()


        request = self.context.get("request")
        if request and request.user.is_authenticated:
            if request.user.groups.filter(name="Faculty").exists():
                fields.pop("urls", None)
        return fields

    def create(self, validated_data):

        enrollment = Enrollment.objects.create(**validated_data)
        Result.objects.create(enrollment=enrollment)

        assessments = Assessment.objects.filter(allocation=enrollment.allocation)
        if assessments.exists():
            for each in assessments:
                AssessmentChecked.objects.create(enrollment=enrollment, assessment=each)

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
            'faculty',
            'course',
            'semester',
            'session',
            'status',
            'passing_threshold',
            'assessment_set',
            'enrollment_set',
            'lecture_set',

        ]

    def get_fields(self):
        fields = super().get_fields()

        if not self.context.get('request'):
            fields['semester'].queryset = Semester.objects.none()
            return fields

        queryset = Semester.objects.filter(status='Inactive', session__status='Initiated')
        if queryset.exists():
            fields['semester'].queryset = queryset
        else:
            fields['semester'].queryset = Semester.objects.none()

        return fields

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        request = self.context.get("request")
        if request and (request.method == 'PUT' or request.method == 'PATCH') and request.user.groups.filter(name="Faculty").exists() and isinstance(self.instance, CourseAllocation):
            extra_kwargs = {
                'faculty':{'read_only': True},
                'course':{'read_only': True},
                'semester':{'read_only': True},
                'status':{'read_only': True},
                'session':{'read_only': True},
                # The cutoff stays writable while the allocation is locked —
                # that is the whole point of the locked window, so results can
                # be recalculated. It freezes when the semester closes.
                'passing_threshold': {'read_only': self.instance.status == 'Completed'},
            }
        if request and request.user.groups.filter(name="Admin").exists():
            extra_kwargs = {
                'session' : {'read_only': True},
                'status' : {'read_only': True},
                # The cutoff is the teacher's academic judgement, not admin's.
                'passing_threshold' : {'read_only': True},
            }
            # An admin editing an existing allocation may only reassign the
            # teacher. Course and semester come from the scheme of studies, and
            # moving either would silently relocate any enrollments hanging off
            # this allocation.
            if request.method in ('PUT', 'PATCH') and isinstance(self.instance, CourseAllocation):
                extra_kwargs['course'] = {'read_only': True}
                extra_kwargs['semester'] = {'read_only': True}

        return extra_kwargs

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not isinstance(self.instance, CourseAllocation):
            self.fields.pop('enrollment_set')
            self.fields.pop('lecture_set')
            self.fields.pop('assessment_set')



    def create(self, validated_data):
        semester = validated_data['semester']

        validated_data['session'] = semester.session
        course = validated_data['course']
        allowed_courses = Course.objects.filter(semesterdetails__semester=semester.semester_id)
        if not allowed_courses.exists():
            raise serializers.ValidationError(f"Semester: {semester} has no available courses")
        if course not in allowed_courses.all():
            raise serializers.ValidationError(f"Course: {course} is not allowed for the Semester: {semester}\n Available courses:\n"
           
                                               f"{", ".join(each.course_code for each in allowed_courses)}\n")

        from .tasks import cache_semester_enrollment_data_task

        allocation = CourseAllocation.objects.create(**validated_data)
        cache_semester_enrollment_data_task.delay(semester.semester_id)

        return allocation


class BulkCourseAllocationListSerializer(serializers.ListSerializer):
    """Cross-row validation and the batched write for the allocation worksheet.

    Writing is only open while the session is Initiated — that is the window in
    which allocations are set up, and nothing references them yet. Once the
    session goes Available, enrollment opens against these allocations and the
    worksheet becomes read-only; a single faculty correction then goes through
    the per-allocation endpoint instead.
    """

    WRITABLE_STATUS = 'Initiated'

    def validate(self, rows):
        if not rows:
            raise serializers.ValidationError('No allocations provided.')

        semesters = {row['semester'].pk: row['semester'] for row in rows}

        sessions = {sem.session_id for sem in semesters.values()}
        if len(sessions) > 1:
            raise serializers.ValidationError(
                'All rows must belong to semesters of a single session.'
            )
        if sessions == {None}:
            raise serializers.ValidationError(
                'These semesters are not bound to any session.'
            )

        session = next(iter(semesters.values())).session
        if session.status != self.WRITABLE_STATUS:
            raise serializers.ValidationError(
                f'Session {session} is {session.status}. Bulk allocation is only open '
                f'while the session is Initiated; afterwards, change a single '
                f'allocation through its own endpoint.'
            )
        self.session = session

        for sem in semesters.values():
            if sem.status != 'Inactive':
                raise serializers.ValidationError(
                    f'Semester {sem} is {sem.status}; allocations are frozen once it activates.'
                )

        # Courses a semester is allowed to run, from its scheme of studies.
        allowed = {}
        for detail in SemesterDetails.objects.filter(
            semester_id__in=semesters, course__isnull=False
        ):
            allowed.setdefault(detail.semester_id, set()).add(detail.course_id)

        existing = {
            (a.semester_id, a.course_id): a
            for a in CourseAllocation.objects.filter(semester_id__in=semesters)
        }

        errors = {}
        seen = set()
        for index, row in enumerate(rows):
            key = (row['semester'].pk, row['course'].pk)

            if key in seen:
                errors[index] = f'Duplicate row for course {row["course"]} in this semester.'
                continue
            seen.add(key)

            if row['course'].pk not in allowed.get(row['semester'].pk, set()):
                errors[index] = (
                    f'Course {row["course"]} is not in the scheme of studies for '
                    f'semester {row["semester"]}.'
                )
                continue

        if errors:
            logger.warning(
                'Rejected bulk allocation payload for session_id=%s: %s row error(s)',
                session.id, len(errors)
            )
            raise serializers.ValidationError(errors)

        self.existing = existing
        return rows

    @transaction.atomic
    def create(self, validated_data):
        to_create, to_update = [], []

        for row in validated_data:
            key = (row['semester'].pk, row['course'].pk)
            current = self.existing.get(key)

            if current is None:
                to_create.append(CourseAllocation(
                    semester=row['semester'],
                    course=row['course'],
                    faculty=row['faculty'],
                    # `session` is a denormalised CharField, not an FK.
                    session=str(row['semester'].session),
                    status='Inactive',
                ))
            elif current.faculty_id != row['faculty'].pk:
                current.faculty = row['faculty']
                to_update.append(current)

        if to_create:
            CourseAllocation.objects.bulk_create(to_create)
        if to_update:
            # faculty is the only field an admin may change; status and session
            # stay under the lifecycle's control.
            CourseAllocation.objects.bulk_update(to_update, ['faculty'])

        from .tasks import cache_semester_enrollment_data_task

        # This cache key is per class and never expires, so it must be
        # refreshed once for every semester the batch touched.
        for semester_id in {row['semester'].pk for row in validated_data}:
            cache_semester_enrollment_data_task.delay(semester_id)

        logger.info(
            'Bulk allocation for session_id=%s: %s created, %s updated',
            self.session.id, len(to_create), len(to_update)
        )
        return {'created': len(to_create), 'updated': len(to_update)}


class BulkCourseAllocationSerializer(serializers.Serializer):
    """One row of the allocation worksheet: which teacher runs which course."""
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())
    faculty = serializers.PrimaryKeyRelatedField(queryset=Faculty.objects.all())

    class Meta:
        list_serializer_class = BulkCourseAllocationListSerializer



class TranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcript
        fields = [
            'student',
            'semester',
            'total_credits',
            'semester_gpa'
        ]
        extra_kwargs = {
            'total_credits' : {'read_only': True},
            'semester_gpa' : {'read_only': True},
        }

    def validate(self, data):
        transcript = Transcript.objects.filter(semester=data['semester'], student=data['student']).first()
        if transcript:
            raise serializers.ValidationError('Transcript already exists')
        return data

    def create(self, validated_data):
        student = Student.objects.get(student_id=validated_data['student'])
        semester = Semester.objects.get(semester_id=validated_data['semester'])

        if not student:
            raise serializers.ValidationError('Student not found')
        if not semester:
            raise serializers.ValidationError('Semester not found')


        semester_gpa = 0.00
        total_credits_attempted = 0.0
        enrollments = Enrollment.objects.filter(student=student, allocation__semester=semester).prefetch_related('result')
        if enrollments.exists() and all(each.status == 'Completed' for each in enrollments):
            for each in enrollments:
                semester_gpa += each.result.course_gpa * each.allocation.course.credit_hours
                total_credits_attempted += each.allocation.course.credit_hours

            semester_gpa = semester_gpa/total_credits_attempted
            transcript = Transcript.objects.create(semester=semester, student=student, semester_gpa=semester_gpa, total_credits=total_credits_attempted)
            return transcript
        return None




class BulkTranscriptSerializer(TranscriptGenerationMixin, serializers.Serializer):
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
        semester = Semester.objects.filter(semester_id=self.context.get('semester_id')).first()
        if not semester:
            raise serializers.ValidationError('Semester not found')

        if semester.status == 'Completed':
            raise serializers.ValidationError('Transcripts already exists')

        try:
            return self.generate_transcripts(semester)
        except ValueError as missing:
            # The mixin reports missing results as a per-student map; here that
            # is a validation error, while the closing task treats it as a
            # signal to calculate them first.
            raise serializers.ValidationError(missing.args[0])


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
            Notification.objects.create(
                recipient=instance.requested_by,
                verb='change_request_declined',
                message=f'Your {instance.get_change_type_display()} request has been declined.',
                level='info',
                content_type=ContentType.objects.get_for_model(ChangeRequest),
                object_id=instance.pk,
            )
            return instance

        if validated_data['status'] == 'applied':
            if instance.change_type == 'faculty_delete':
                if instance.target_faculty:
                    from .tasks import delete_faculty_task
                    person_id = instance.target_faculty.employee_id.person_id
                    instance.target_faculty = None
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    delete_faculty_task.delay(person_id)
                    return instance

            if instance.change_type == 'student_delete':
                if instance.target_student:
                    from .tasks import delete_student_task
                    person_id = instance.target_student.student_id.person_id
                    instance.target_student = None
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    delete_student_task.delay(person_id)
                    return instance

            if instance.change_type == 'hod_change':
                if instance.new_hod:
                    old_hod = instance.department.HOD if instance.department.HOD else None
                    department = get_object_or_404(Department, department_id=instance.department.department_id)
                    department.HOD = instance.new_hod
                    department.save()
                    instance.status = 'applied'
                    instance.applied_at = timezone.now()
                    instance.save()
                    from .tasks import send_hod_change_mail
                    send_hod_change_mail.apply_async(args=[instance.pk, old_hod.pk if old_hod else None], eta=timezone.now()+timedelta(minutes=2))

                    hod_change_content_type = ContentType.objects.get_for_model(ChangeRequest)
                    if instance.new_hod.employee_id.user_id:
                        Notification.objects.create(
                            recipient=instance.new_hod.employee_id.user,
                            verb='hod_change_applied',
                            message=f'You are now the Head of Department for {department.department_name}.',
                            level='info',
                            content_type=hod_change_content_type,
                            object_id=instance.pk,
                        )
                    if old_hod is not None and old_hod.employee_id.user_id:
                        Notification.objects.create(
                            recipient=old_hod.employee_id.user,
                            verb='hod_change_applied',
                            message=f'Your role as Head of Department for {department.department_name} has ended.',
                            level='info',
                            content_type=hod_change_content_type,
                            object_id=instance.pk,
                        )
        return instance



class FacultyStudentBulkSerializer(serializers.Serializer):
    file = serializers.FileField()
    class Meta:
        fields = [
            'file'
        ]

    def validate(self, data):
        file = data['file']
        if not (file.name.endswith('.csv') or file.name.endswith('.xlsx')):
            raise serializers.ValidationError('Invalid file type')

        if not file.content_type == 'text/csv' or file.content_type == 'application/vnd.ms-excel':
            raise serializers.ValidationError('Invalid file type')

        return data

    def create(self, validated_data):
        insert_count = 0
        error_row_count = 0
        row_count = 0
        error_rows = []
        file = validated_data['file']
        logger.info('Processing bulk upload file=%s', file.name)
        if file.name.endswith('.csv'):
            decoded_file = io.TextIOWrapper(file.file, encoding='utf-8-sig')
            file_data = csv.DictReader(decoded_file)

            if self.context.get('target_model')== 'faculty':
                serializer_class = FacultySerializer
            elif self.context.get('target_model')== 'student':
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
        if 'department' in row:
            parsed_row['department'] = row['department']
            if 'joining_date' in row and row['joining_date'] != '':
                parsed_row['joining_date'] = row['joining_date']

        #parsing student_data if present
        if 'program' in row:
            parsed_row['program'] = row['program']
        if 'student_class' in row:
            parsed_row['student_class'] = row['student_class']
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


class CurrentSessionSerializer(serializers.ModelSerializer):
    availability_deadline = serializers.DateTimeField(read_only=True)

    class Meta:
        model = AcademicSession
        # The three deadlines in the order the session passes them: Initiated
        # -> Available, Available -> Active, Active -> Completed. A client
        # sitting in the enrollment window needs the activation date to say
        # when that window shuts, so all three are here rather than the ends
        # alone.
        fields = [
            'id',
            'period',
            'year',
            'status',
            'availability_deadline',
            'activation_deadline',
            'closing_deadline',
        ]
        read_only_fields = fields


class SessionSerializer(serializers.ModelSerializer):
    # How long before the closing deadline each pending-results nudge fires.
    REMINDER_SCHEDULE = [
        (timedelta(days=2), '2 days'),
        (timedelta(days=1), '1 day'),
        (timedelta(hours=6), '6 hours'),
    ]

    url = serializers.HyperlinkedIdentityField(
        view_name='Admin:session-detail',
        lookup_field='id',
    )
    availability_deadline = serializers.DateTimeField(read_only=True)

    class Meta:
        model = AcademicSession
        fields = [
            'url',
            'id',
            'period',
            'year',
            'status',
            'activation_deadline',
            'availability_delta',
            'availability_deadline',
            'closing_deadline',
        ]
        extra_kwargs = {
            'status': {'read_only': True},
        }

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, AcademicSession):
            extra_kwargs['year'] = {'read_only': True}
            if self.instance.status != 'Active':
                extra_kwargs['closing_deadline'] = {'read_only': True}
        return extra_kwargs

    def validate_year(self, value):
        if value < timezone.now().year:
            raise serializers.ValidationError('Year cannot be in the past')
        return value

    def validate_activation_deadline(self, value):
        # At least two weeks out, so the default one-week availability window
        # still has a full week of runway ahead of it.
        if value and value < timezone.now() + timedelta(weeks=2):
            raise serializers.ValidationError('Activation deadline must be at least 2 weeks ahead')
        if value and value > timezone.now() + timedelta(weeks=4):
            raise serializers.ValidationError('Activation deadline cannot be more than 4 weeks in the future')
        return value

    def validate(self, data):
        if 'availability_delta' not in data:
            return data

        activation = data.get('activation_deadline') or (
            self.instance.activation_deadline if self.instance else None
        )

        # The delta only means anything relative to an activation deadline.
        if not activation:
            raise serializers.ValidationError({
                'availability_delta':
                    'Cannot be set until the session has an activation deadline.'
            })

        # Once activation has passed, the enrollment window is already open or
        # over — moving its start would rewrite history.
        if activation <= timezone.now():
            raise serializers.ValidationError({
                'availability_delta':
                    'Cannot be changed once the activation deadline has passed.'
            })

        # The window has to start in the future, so the delta cannot reach back
        # further than the activation deadline itself.
        if activation - timedelta(days=data['availability_delta']) <= timezone.now():
            raise serializers.ValidationError({
                'availability_delta':
                    f'A {data["availability_delta"]}-day window would open before now; '
                    f'the activation deadline is {activation:%Y-%m-%d %H:%M}.'
            })

        return data

    def validate_closing_deadline(self, value):
        # Clearing the deadline would strand the locking cascade and the
        # scheduled reminders, which are already keyed to it.
        if value is None:
            raise serializers.ValidationError('Closing deadline cannot be cleared.')
        # At least a week out, so the 2-day, 1-day and 6-hour reminders all
        # have room to fire before results are calculated automatically.
        if value < timezone.now() + timedelta(weeks=1):
            raise serializers.ValidationError('Closing deadline must be at least 1 week ahead')
        if value > timezone.now() + timedelta(weeks=4):
            raise serializers.ValidationError('Closing deadline cannot be more than 4 weeks ahead')
        if self.instance and self.instance.activation_deadline and value <= self.instance.activation_deadline:
            raise serializers.ValidationError('Closing deadline must be after activation deadline')
        return value

    def update(self, instance, validated_data):
        if 'activation_deadline' in validated_data:
            # Only one session may be live (Initiated -> Available -> Active)
            # at a time. Nothing enforces this at the DB level — this model's
            # only constraint is unique(period, year) — so it is guarded here,
            # at the single point where a session becomes live.
            clash = (
                AcademicSession.objects
                .filter(status__in=['Initiated', 'Available', 'Active'])
                .exclude(pk=instance.pk)
                .first()
            )
            if clash:
                logger.warning(
                    'Rejected activation_deadline for session_id=%s: session_id=%s is already %s',
                    instance.id, clash.id, clash.status
                )
                raise serializers.ValidationError(
                    f'Session {clash} is currently {clash.status}. Only one session can be '
                    f'live at a time, complete it before initiating another.'
                )

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.status = 'Initiated'
            instance.save()

            activation_cache_key = f'session:activation:{instance.id}'
            old_task_id = cache.get(activation_cache_key)
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(activation_cache_key)
                logger.info('Revoked previous activation task for session_id=%s', instance.id)

            availability_cache_key = f'session:availability:{instance.id}'
            old_task_id = cache.get(availability_cache_key)
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(availability_cache_key)
                logger.info('Revoked previous availability task for session_id=%s', instance.id)

            from .tasks import session_activation_task, session_availability_task

            task = session_activation_task.apply_async(args=[instance.id], eta=instance.activation_deadline)
            cache.set(activation_cache_key, task.id, timeout=None)

            task = session_availability_task.apply_async(args=[instance.id], eta=instance.availability_deadline)
            cache.set(availability_cache_key, task.id, timeout=None)

            logger.info(
                'Session %s set to Initiated: activation scheduled for %s, availability for %s',
                instance.id, instance.activation_deadline, instance.availability_deadline
            )
            return instance

        if 'closing_deadline' in validated_data:
            instance.closing_deadline = validated_data['closing_deadline']
            instance.save()

            closing_cache_key = f'session:closing:{instance.id}'
            old_task_id = cache.get(closing_cache_key)
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(closing_cache_key)
                logger.info('Revoked previous closing task for session_id=%s', instance.id)

            from .tasks import (
                session_closing_task, session_locking_task,
                pending_results_reminder_task,
            )

            task = session_closing_task.apply_async(args=[instance.id], eta=instance.closing_deadline)
            cache.set(closing_cache_key, task.id, timeout=None)

            # Setting a closing deadline freezes the session's coursework:
            # assessments and marks stop moving, while faculty keep the window
            # to calculate results and adjust passing_threshold.
            session_locking_task.delay(instance.id)

            # Escalating nudges for allocations still without results. The
            # 1-week minimum on closing_deadline guarantees every one of these
            # lands in the future.
            for delta, remaining in self.REMINDER_SCHEDULE:
                old_task_id = cache.get(f'session:reminder:{remaining}:{instance.id}')
                if old_task_id:
                    app.control.revoke(old_task_id, terminate=True)
                    cache.delete(f'session:reminder:{remaining}:{instance.id}')

                reminder = pending_results_reminder_task.apply_async(
                    args=[instance.id, remaining],
                    eta=instance.closing_deadline - delta,
                )
                cache.set(f'session:reminder:{remaining}:{instance.id}', reminder.id, timeout=None)

            logger.info('Session %s closing scheduled for %s', instance.id, instance.closing_deadline)
            return instance

        if 'availability_delta' in validated_data:
            # Moving the delta moves when enrollment opens, so the already
            # queued availability task has to be re-aimed — otherwise it fires
            # at the moment the old delta implied.
            instance = super().update(instance, validated_data)

            availability_cache_key = f'session:availability:{instance.id}'
            old_task_id = cache.get(availability_cache_key)
            if old_task_id:
                app.control.revoke(old_task_id, terminate=True)
                cache.delete(availability_cache_key)

            from .tasks import session_availability_task
            task = session_availability_task.apply_async(
                args=[instance.id], eta=instance.availability_deadline,
            )
            cache.set(availability_cache_key, task.id, timeout=None)

            logger.info(
                'Session %s availability re-scheduled for %s (delta now %s day(s))',
                instance.id, instance.availability_deadline, instance.availability_delta,
            )
            return instance

        return super().update(instance, validated_data)


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
        # A semester's session is fixed once the session has gone live —
        # activation is driven from the session, so rebinding it mid-flight
        # would strand the semester in the wrong lifecycle.
        if isinstance(self.instance, Semester) and self.instance.status != 'Inactive':
            extra_kwargs['session'] = {'read_only': True}
        return extra_kwargs

    @extend_schema_field(OpenApiTypes.URI)
    def get_transcript_generation_url(self, obj):
        request = self.context.get("request")
        return request.build_absolute_uri(
            reverse("Admin:semester-transcripts-create", kwargs={"semester_id": obj.semester_id})
        )

    def get_associated_class(self, obj) -> str:
        if obj.associated_class:
            return str(obj.associated_class)
        return 'None'



    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)

        if not isinstance(self.instance, Semester):
            self.fields.pop('transcript_generation_url')
            self.fields.pop('courseallocation_set')
            self.fields.pop('transcript_set')
        elif not (self.instance.session and self.instance.session.closing_deadline):
            # Transcripts belong to the closing window. Until the session has a
            # closing deadline the coursework is not locked, results are still
            # being entered, and generating now would capture a half-graded
            # semester.
            self.fields.pop('transcript_generation_url')

