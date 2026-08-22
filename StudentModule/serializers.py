import logging
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import  serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from Models.models import *

logger = logging.getLogger(__name__)




class ReviewHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.review_id is None:
            return None
        kwargs = {
            'student_id': obj.enrollment.student.student_id,
            'enrollment_id' : obj.enrollment.enrollment_id,
            'review_id': getattr(obj, self.lookup_field)
        }
        return self.reverse (view_name, kwargs=kwargs, request=request, format=format)



class ReviewsSerializer(serializers.ModelSerializer):
    urls = ReviewHyperlinkedIdentityField(
        view_name='Student:review-detail',
        lookup_field='review_id',
    )
    class Meta:
        model = Reviews
        ref_name = 'ReviewDetail'
        fields = [
            'urls',
            'review_id',
            'enrollment',
            'review_text',
            'rating',
            'timestamp',
        ]
        extra_kwargs = {
            'review_id' : {'read_only': True},
            'enrollment': {'read_only': True},
            'timestamp' : {'read_only': True},
        }

    def create(self, validated_data):
        enrollment = Enrollment.objects.filter(enrollment_id=self.context.get('enrollment_id')).first()
        if not enrollment:
            raise serializers.ValidationError("Enrollment does not exist")

        review = Reviews.objects.create(review_text=validated_data.get('review_text'),
                                        rating=validated_data.get('rating'),
                                        enrollment=enrollment)

        return review





class AssessmentCheckedHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.assessment_id is None:
            return None
        kwargs = {
            'enrollment_id' : obj.enrollment.enrollment_id,
            'assessment_id': obj.assessment.assessment_id,
            'id': getattr(obj, self.lookup_field)
        }
        return self.reverse(view_name, kwargs=kwargs, request=request, format=format)


class StudentAssessmentCheckedSerializer(serializers.ModelSerializer):
    urls = AssessmentCheckedHyperlinkedIdentityField(
        view_name='Student:assessment-upload',
        lookup_field='id',
    )
    class Meta:
        model = AssessmentChecked
        fields = [
            'urls',
            'id',
            'assessment',
            'enrollment',
            'obtained',
            'student_upload'
        ]
        extra_kwargs = {
            'assessment': {'read_only': True},
            'enrollment': {'read_only': True},
            'obtained': {'read_only': True},
        }

    def validate_student_upload(self, value):
        instance = getattr(self, 'instance', None)
        if value is None and (instance is None or instance.student_upload is None):
            return None

        if instance and value == instance.student_upload:
            return value

        if value is None and instance and instance.student_upload:
            return instance.student_upload

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


    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        method = self.context.get('method')
        if self.instance and (method in ['PUT', 'PATCH'] ):
            if (self.instance.assessment.student_submission and self.instance.assessment.submission_deadline is not None and (self.instance.assessment.submission_deadline < timezone.now())) or self.instance.enrollment.status == 'Completed':
                    extra_kwargs['student_upload'] = {'read_only': True}
        return extra_kwargs


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance:
            if not self.instance.assessment.student_submission or (self.instance.assessment.student_submission and self.instance.assessment.submission_deadline < timezone.now()) or self.instance.enrollment.status == 'Completed':
                self.fields.pop('urls')

class StudentAssessmentSerializer(serializers.ModelSerializer):
    assessmentchecked_set = StudentAssessmentCheckedSerializer(many=True, read_only=True)
    class Meta:
        model = Assessment
        fields = [
            'assessment_id',
            'assessment_type',
            'assessment_name',
            'assessment_date',
            'total_marks',
            'submission_deadline',
            'assessmentchecked_set'
        ]



    def to_representation(self, instance):
        representation = super().to_representation(instance)
        request = self.context.get('request')
        if request:
            representation['assessmentchecked_set'] = StudentAssessmentCheckedSerializer(
                instance=
                instance.assessmentchecked_set.filter(
                    assessment=instance.assessment_id,
                    enrollment__student__student_id__user=request.user
                ).first(), context=self.context
            ).data

        if not instance.submission_deadline or instance.submission_deadline < timezone.now():
            representation.pop('submission_deadline')
        return representation


class StudentCourseAllocationSerializer(serializers.ModelSerializer):
    faculty_details  = serializers.SerializerMethodField(read_only=True)
    course_details = serializers.SerializerMethodField(read_only=True)
    assessment_set = StudentAssessmentSerializer(many=True, read_only=True)

    class Meta:
        model = CourseAllocation
        fields = [
            'faculty_details',
            'course_details',
            'semester_id',
            'session',
            'assessment_set'
        ]

    @extend_schema_field(
        inline_serializer(
            name='FacultyData',
            fields={
                'teacher_id': serializers.CharField(),
                'first_name': serializers.CharField(),
                'last_name': serializers.CharField(),
            }
        )
    )
    def get_faculty_details(self, obj):
        if obj.faculty:
            return {
                'teacher_id' : obj.faculty.employee_id.person_id,
                'first_name' : obj.faculty.employee_id.first_name,
                'last_name' : obj.faculty.employee_id.last_name,
            }
        return {}

    @extend_schema_field(
        inline_serializer(
            name='CourseData',
            fields={
                'course_code': serializers.CharField(),
                'course_name': serializers.CharField(),
                'credit_hours': serializers.IntegerField(),
                'lab' : serializers.BooleanField(),
                'pre_requisite': serializers.CharField(),
            }
        )
    )
    def get_course_details(self, obj):
        if obj.course:
            return {
                'course_code' : obj.course.course_code,
                'course_name' : obj.course.course_name,
                'credit_hours' : obj.course.credit_hours,
                'lab' : obj.course.lab,
                'pre_requisite' : obj.course.pre_requisite.course_code if obj.course.pre_requisite else None,
            }
        return None


class StudentEnrollmentSerializer(serializers.ModelSerializer):
    allocation_details = StudentCourseAllocationSerializer(
        source='allocation', read_only=True
    )
    url = serializers.HyperlinkedIdentityField(
        view_name='Student:enrollment-detail',
        lookup_field='enrollment_id',
    )
    result = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = Enrollment
        fields = [
            'url',
            'enrollment_id',
            'student',
            'allocation',
            'status',
            'allocation_details',
            'result',
        ]

    @extend_schema_field(
        inline_serializer(
            name='EnrollmentResult',
            fields={
                'course_gpa': serializers.DecimalField(max_digits=4, decimal_places=2, allow_null=True),
                'obtained_marks': serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True),
            }
        )
    )
    def get_result(self, obj):
        if obj.status != 'Completed':
            return None
        try:
            result = obj.result
        except Result.DoesNotExist:
            return None
        return {
            'course_gpa': result.course_gpa,
            'obtained_marks': result.obtained_marks,
        }


class StudentTranscriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcript
        fields = [
            'id',
            'semester',
            'total_credits',
            'semester_gpa',
        ]


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = [
            'lecture',
            'attendance_date',
            'is_present'
        ]


class StudentAttendanceSerializer(serializers.ModelSerializer):
    faculty_details = serializers.SerializerMethodField(read_only=True)
    course_details = serializers.SerializerMethodField(read_only=True)
    attendance_details = serializers.SerializerMethodField(read_only=True)
    percentage = serializers.SerializerMethodField(read_only=True)
    url = serializers.HyperlinkedIdentityField(
        view_name='Student:attendance-detail',
        lookup_field='enrollment_id',
    )
    class Meta:
        model = Enrollment
        fields = [
            'url',
            'faculty_details',
            'course_details',
            'attendance_details',
            'percentage'
        ]

    @extend_schema_field(
        inline_serializer(
            name='FacultyData',
            fields={
                'faculty_id': serializers.CharField(),
                'first_name': serializers.CharField(),
                'last_name': serializers.CharField(),
            }
        )
    )
    def get_faculty_details(self, obj):
        if obj:
            return {
                'faculty_id' : obj.allocation.faculty.employee_id.person_id,
                'first_name' : obj.allocation.faculty.employee_id.first_name,
                'last_name' : obj.allocation.faculty.employee_id.last_name,
            }
        return None

    @extend_schema_field(
        inline_serializer(
            name='CourseData',
            fields={
                'course_code': serializers.CharField(),
                'course_name': serializers.CharField(),
                'credit_hours': serializers.IntegerField(),
            }
        )
    )
    def get_course_details(self, obj):
        if obj:
            return {
                'course_code' : obj.allocation.course.course_code,
                'course_name' : obj.allocation.course.course_name,
                'credit_hours' : obj.allocation.course.credit_hours,
            }
        return None

    @extend_schema_field(AttendanceSerializer(many=True))
    def get_attendance_details(self, obj):
        if obj:
            attendance = Attendance.objects.filter(enrollment=obj, lecture__allocation=obj.allocation)
            return AttendanceSerializer(attendance, many=True).data
        return None


    def get_percentage(self, obj) -> float:
        if obj:
            attendance = Attendance.objects.filter(enrollment=obj, lecture__allocation=obj.allocation)
            total = attendance.count()
            attended = attendance.filter(is_present=True).count()
            return round((attended / total) * 100) if total else 0
        return 0.0



class StudentEnrollmentCreateSerializerB(serializers.Serializer):
    allocation_id = serializers.IntegerField()
    confirm = serializers.BooleanField()
    class Meta:
        fields = [
            'allocation_id',
            'confirm',
        ]

    def create(self, validated_data):
        if not validated_data:
            return None
        count = 0
        return_id = None
        request = self.context.get('request')
        student = request.student
        allocation_ids = self.context.get('allocation_ids')
        enrolled_allocation_ids = self.context.get('enrolled_allocations_ids')
        if validated_data['allocation_id'] in allocation_ids:
            if validated_data['allocation_id'] in enrolled_allocation_ids and not validated_data['confirm']:
                Enrollment.objects.get(allocation_id=validated_data['allocation_id'], student=student).delete()
                count = -1
            if validated_data['allocation_id'] not in enrolled_allocation_ids and validated_data['confirm']:
                Enrollment.objects.create(allocation_id=validated_data['allocation_id'], student=student)
                count = 1
            return_id = validated_data['allocation_id']

        return {'count': count, 'allocation_id': return_id}


