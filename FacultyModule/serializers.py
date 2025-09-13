from datetime import timedelta
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.shortcuts import get_list_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field, inline_serializer

from AdminModule.mixins import ResultCalculationMixin
from Models.models import *
from rest_framework import serializers, status

def get_faculty_allocation_serializer():
    from AdminModule.serializers import CourseAllocationSerializer
    from rest_framework.reverse import reverse

    class FacultyCourseAllocationSerializer(CourseAllocationSerializer):
        urls = serializers.HyperlinkedIdentityField(
            view_name='Faculty:allocation-detail',
            lookup_field='allocation_id'
        )

        result_calculation_url = serializers.SerializerMethodField()

        @extend_schema_field(OpenApiTypes.URI)
        def get_result_calculation_url(self, obj):
            request = self.context.get("request")
            return request.build_absolute_uri(
                reverse("Faculty:allocation-calculate-result", kwargs={"allocation_id": obj.allocation_id})
            )

        class Meta(CourseAllocationSerializer.Meta):
            fields = CourseAllocationSerializer.Meta.fields + ["urls", "result_calculation_url"]
            ref_name = "FacultyCourseAllocationUnique"

    return FacultyCourseAllocationSerializer



class CustomizedListSerializer(serializers.ListSerializer):
    def run_child_validation(self, data):
        if hasattr(self, 'instance') and self.instance:
            try:
                child_instance = self.instance.get(id=data['id'])
            except ObjectDoesNotExist:
                child_instance = None
        else:
            child_instance = None

        child = self.child.__class__(instance=child_instance, context=self.context)
        return child.run_validation(data)



class AssessmentCheckedSerializer(serializers.ModelSerializer):
    student_info = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = AssessmentChecked
        fields = [
            'id',
            'assessment_id',
            'enrollment_id',
            'obtained',
            'student_upload',
            'student_info',
        ]
        list_serializer_class = CustomizedListSerializer

    @extend_schema_field(
        inline_serializer(
            name='StudentData',
            fields={
                'image': serializers.URLField(),
                'student_id': serializers.CharField(),
                'first_name': serializers.CharField(),
                'last_name': serializers.CharField(),
            }
        )
    )
    def get_student_info(self, obj):
        request = self.context.get("request")
        if obj:
            return {
                'image' : request.build_absolute_uri(obj.enrollment_id.student_id.student_id.image.url) if obj.enrollment_id.student_id.student_id.image else None,
                'student_id' : obj.enrollment_id.student_id.student_id.person_id,
                'first_name' : obj.enrollment_id.student_id.student_id.first_name,
                'last_name' : obj.enrollment_id.student_id.student_id.last_name,
            }
        return None


    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if self.instance and self.context.get('request').user.groups.filter(name='Faculty').exists():
            extra_kwargs = {
                'student_upload' : {'read_only': True},

            }

        return extra_kwargs


    def validate_obtained(self, value):
        if self.instance and self.instance.obtained == value:
            return value
        if self.instance and value and value > self.instance.assessment_id.total_marks:
            raise serializers.ValidationError("Obtained marks exceeds total marks")

        return value


class AssessmentHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.allocation_id is None:
            return None
        kwargs = {
            'allocation_id': obj.allocation_id.pk,
            'assessment_id': getattr(obj, self.lookup_field)
        }
        return self.reverse(view_name, kwargs=kwargs, request=request, format=format)



class AssessmentSerializer(serializers.ModelSerializer):
    assessmentchecked_set = AssessmentCheckedSerializer(many=True, required=False)
    urls = AssessmentHyperlinkedIdentityField(
        view_name='Faculty:assessment-detail',
        lookup_field='assessment_id'
    )
    class Meta:
        model = Assessment
        fields = [
            'urls',
            'assessment_id',
            'allocation_id',
            'assessment_type',
            'assessment_name',
            'assessment_date',
            'weightage',
            'total_marks',
            'file_upload',
            'student_submission',
            'submission_deadline',
            'assessmentchecked_set'
        ]
        extra_kwargs = {
            'allocation_id': {'read_only': True},
        }

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Assessment):
            extra_kwargs = {
                'allocation_id' : {'read_only': True},
                'assessment_type' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'assessment_name' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'assessment_date' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'weightage' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'total_marks' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'file_upload' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'student_submission' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'submission_deadline' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
                'assessmentchecked_set' : {'read_only': True} if self.instance.allocation_id.status == 'Completed' else {'read_only': False},
            }

        return extra_kwargs


    def validate_submission_deadline(self, value):
        if not value:
            return self.instance.submission_deadline if self.instance.submission_deadline else None

        if self.instance and self.instance.submission_deadline == value:
            return value
        if value <= timezone.now():
            raise serializers.ValidationError("submission deadline cannot be in the past")
        return value

    def validate_total_marks(self, value):
        if self.instance and self.instance.total_marks == value:
            return value
        if value is not None and value < 0:
            raise serializers.ValidationError('Total marks must be a positive number.')
        if value is not None and value > 500:
            raise serializers.ValidationError('Total marks cannot be greater than 500.')
        return value

    def validate(self,data):
        allocation_id = self.context.get('allocation_id')
        errors = {}
        if self.instance and self.instance.weightage == data['weightage'] and self.instance.assessment_name == data['assessment_name']:
            return data

        #weightage lower threshold validation
        if data['weightage'] < 1:
            errors['weightage'] = 'Weightage cannot be less than 1.'

        all_assessments = Assessment.objects.filter(allocation_id=allocation_id)
        total_weightage = 0
        if all_assessments.exists():
            total_weightage = sum([each.weightage for each in all_assessments])

        #weightage upper threshold validation
        if total_weightage + data['weightage'] > 100:
            errors['weightage'] = f'Total weightage: {total_weightage+data['weightage']}, Error: Total weightage cannot exceed 100 for allocation_id: {data["allocation_id"]}'

        #assessment name uniqueness validation
        same_assessment = all_assessments.filter(assessment_type=data['assessment_type']).filter(assessment_name=data['assessment_name'])
        if same_assessment.exists():
            errors['assessment_name'] = f"Assessment {data['assessment_name']} already exists for the allocation_id: {data['allocation_id']}"

        #submission deadline validation provided student_submission == True
        if data['student_submission'] == True and not data['submission_deadline']:
            errors['submission_deadline'] = 'Submission deadline cannot be null'
        if errors:
            raise serializers.ValidationError(errors)
        return data

    def validate_assessment_date(self,value):
        if self.instance and self.instance.assessment_date == value:
            return value

        if value is not None and value > datetime.now().date() + timedelta(days=30):
            raise serializers.ValidationError(f'Cannot schedule more than a month ahead')

        if value is not None and value < datetime.now().date():
            raise serializers.ValidationError(f'Cannot schedule be in the past')
        return value


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
        self.fields['assessmentchecked_set'].context.update(self.context)

        if not isinstance(self.instance, Assessment):
            self.fields.pop('assessmentchecked_set')

        if self.instance and hasattr(self.instance, 'assessmentchecked_set'):
            self.fields['assessmentchecked_set'].instance = self.instance.assessmentchecked_set.all()

    def create(self, validated_data):
        validated_data['allocation_id'] = CourseAllocation.objects.get(allocation_id=self.context.get('allocation_id'))
        assessment = Assessment.objects.create(**validated_data)
        enrollment_set = get_list_or_404(Enrollment, allocation_id=assessment.allocation_id)
        for enrollment in enrollment_set:
            AssessmentChecked.objects.create(enrollment_id=enrollment, assessment_id=assessment)

        return assessment

    def update(self, instance, validated_data):
        if not validated_data['student_submission']:
            validated_data.pop('student_submission')
            validated_data.pop('submission_deadline')

        if 'assessmentchecked_set' in validated_data:
            assessmentChecked_data = validated_data.pop('assessmentchecked_set')
            if assessmentChecked_data and instance.assessmentchecked_set.exists():
                for each in instance.assessmentchecked_set.all():
                    data = next(
                        (item for item in assessmentChecked_data if item["enrollment_id"] == each.enrollment_id), None)
                    if data:
                        each.obtained = data['obtained']
                        each.save()

        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
            instance.save()

        return instance


class AttendanceSerializer(serializers.ModelSerializer):
    student_info = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = Attendance
        fields = [
            'id',
            'attendance_date',
            'lecture_id',
            'student_id',
            'is_present',
            'student_info'
        ]
        list_serializer_class = CustomizedListSerializer

    @extend_schema_field(
        inline_serializer(
            name='StudentData',
            fields={
                'image': serializers.URLField(),
                'student_id': serializers.CharField(),
                'first_name': serializers.CharField(),
                'last_name': serializers.CharField(),
            }
        )
    )
    def get_student_info(self, obj):
        request = self.context.get('request')
        if obj:
            return {
                'image': request.build_absolute_uri(obj.student_id.student_id.image.url) if obj.student_id.student_id.image else None,
                'student_id': obj.student_id.student_id.person_id,
                'first_name': obj.student_id.student_id.first_name,
                'last_name': obj.student_id.student_id.last_name,
            }


    def validate(self, data):
        allocation = CourseAllocation.objects.filter(allocation_id=data['lecture_id'].allocation_id.allocation_id).prefetch_related('enrollment_set')
        if not allocation.exists():
            raise serializers.ValidationError(f'No course allocations available for lecture: {data['lecture_id']}')

        enrolled_students = allocation.first().enrollment_set.values_list('student_id', flat=True)

        if not allocation.exists() or  data['student_id'].pk not in enrolled_students :
            raise serializers.ValidationError(f'Student {data["student_id"]} does not exist for course allocation: {allocation}')

        return data


class LectureHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.allocation_id is None:
            return None
        kwargs = {
            'allocation_id': obj.allocation_id.pk,
            'lecture_id': getattr(obj, self.lookup_field)
        }
        return self.reverse(view_name, kwargs=kwargs, request=request, format=format)


class LectureSerializer(serializers.ModelSerializer):
    attendance_set = AttendanceSerializer(many=True, required=False)
    urls = LectureHyperlinkedIdentityField(
        view_name='Faculty:lecture-detail',
        lookup_field='lecture_id'
    )
    class Meta:
        model = Lecture
        fields = [
            'urls',
            'lecture_id',
            'lecture_no',
            'allocation_id',
            'starting_time',
            'venue',
            'duration',
            'topic',
            'attendance_set',
        ]
        extra_kwargs = {
            'lecture_id' : {'read_only': True},
            'lecture_no': {'read_only': True},
            'allocation_id': {'read_only': True},
        }

    def validate_starting_time(self,value):
        if self.instance and self.instance.starting_time == value:
            return value
        if value > timezone.now():
            raise serializers.ValidationError(f'Starting time in future')
        return value


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['attendance_set'].context.update(self.context)

        if not isinstance(self.instance, Lecture):
            self.fields.pop('attendance_set')

        if self.instance and hasattr(self.instance, 'attendance_set'):
            self.fields['attendance_set'].instance = self.instance.attendance_set.all()

    @transaction.atomic
    def create(self, validated_data):
        validated_data['allocation_id'] = CourseAllocation.objects.get(allocation_id=self.context.get('allocation_id'))
        lecture_count = Lecture.objects.filter(allocation_id=validated_data['allocation_id']).count()
        lecture_no = lecture_count +1
        lecture_id = f'{validated_data['allocation_id']}-{lecture_no}'
        validated_data['lecture_id'] = lecture_id
        validated_data['lecture_no'] = lecture_no

        print(validated_data)
        attendance_set = {}

        if 'attendance_set' in validated_data:
            attendance_set = validated_data.pop('attendance_set')

        lecture = Lecture.objects.create(**validated_data)
        enrollment = get_list_or_404(Enrollment, allocation_id=validated_data['allocation_id'])

        if attendance_set:
            for each in attendance_set:
                Attendance.objects.create(attendance_date=lecture.starting_time.date(), lecture_id=lecture, **each)

        else:
            for enrollment in enrollment:
                Attendance.objects.create(lecture_id=lecture, student_id=enrollment.student_id,)

        return lecture

    @transaction.atomic
    def update(self, instance, validated_data):
        attendance_set = validated_data.pop('attendance_set')

        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        instance.save()

        if attendance_set and instance.attendance_set.exists():
            for each in instance.attendance_set.all():
                data = next((item for item in attendance_set if item["student_id"] == each.student_id), None)
                if data:
                    for attribute, value in data.items():
                        setattr(each, attribute, value)
                    each.attendance_date = instance.starting_time.date()
                    each.save()

        return instance


class FacultyRequestsSerializer(
    ResultCalculationMixin,
    serializers.ModelSerializer
):
    urls = serializers.HyperlinkedIdentityField(
        view_name='Faculty:change-request-update',
        lookup_field='pk'
    )
    class Meta:
        model = ChangeRequest
        fields = [
            'urls',
            'change_type',
            'status',
            'target_allocation',
            'requested_by',
            'requested_at',
            'confirmed_at',
            'applied_at',
        ]
        extra_kwargs = {
            'change_type': {'read_only': True},
            'target_allocation': {'read_only': True},
            'requested_by': {'read_only': True},
            'requested_at': {'read_only': True},
            'confirmed_at': {'read_only': True},
            'applied_at': {'read_only': True},
        }

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance,ChangeRequest) and self.instance.status != 'confirmed':
            extra_kwargs = {
                'status': {'read_only': True},
            }
        return extra_kwargs

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(self.instance, ChangeRequest):
            if self.instance.status != 'confirmed':
                self.fields.pop('urls')


    def update(self, instance, validated_data):
        if validated_data.get('status') in ['confirmed','pending','expired']:
            return instance

        if validated_data.get('status') == 'expired':
            instance.status = 'expired'
            instance.applied_at = timezone.now()
            instance.save()
            return instance

        if validated_data.get('status') == 'applied':
            allocation = instance.target_allocation
            calculated_result = 0

            if not allocation.enrollment_set.exists():
                instance.status = 'declined'
                instance.applied_at = timezone.now()
                instance.save()
                raise serializers.ValidationError('This allocation has no enrollments')

            for each in allocation.enrollment_set.all():
                if not each.result:
                    Result.objects.create(enrollment_id=each)
                if each.result and each.result.course_gpa and each.result.obtained_marks:
                    calculated_result +=1

            if calculated_result > 1:
                instance.status = 'declined'
                instance.applied_at = timezone.now()
                instance.save()
                raise serializers.ValidationError('This results for this allocation have already been calculated')

            data = {}
            for each in allocation.assessment_set.all():
                for e in each.assessmentchecked_set.all():
                    if not e.obtained:
                        data[e.enrollment_id.student_id.student_id.person_id] = f'marks for assessment: {each.assessment_name} are null'

            if data:
                raise serializers.ValidationError(data)

            result_data = self.calculate_result(allocation)
            instance.status = 'applied'
            instance.applied_at = timezone.now()
            allocation.status = 'Complted'
            allocation.save()
            instance.save()
            print(result_data)

            return instance


