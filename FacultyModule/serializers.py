import logging
from datetime import timedelta, datetime
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.shortcuts import get_list_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field, inline_serializer

from AdminModule.mixins import ResultCalculationMixin
from Models.models import *
from rest_framework import serializers, status

logger = logging.getLogger(__name__)

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
            'assessment',
            'enrollment',
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
                'image' : request.build_absolute_uri(obj.enrollment.student.student_id.image.url) if obj.enrollment.student.student_id.image else None,
                'student_id' : obj.enrollment.student.student_id.person_id,
                'first_name' : obj.enrollment.student.student_id.first_name,
                'last_name' : obj.enrollment.student.student_id.last_name,
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
        if self.instance and value and value > self.instance.assessment.total_marks:
            raise serializers.ValidationError("Obtained marks exceeds total marks")

        return value


class AssessmentHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.allocation is None:
            return None
        kwargs = {
            'allocation_id': obj.allocation.pk,
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
            'allocation',
            'assessment_type',
            'assessment_name',
            'assessment_date',
            'weightage',
            'total_marks',
            'student_submission',
            'submission_deadline',
            'assessmentchecked_set'
        ]
        extra_kwargs = {
            'allocation': {'read_only': True},
        }

    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if isinstance(self.instance, Assessment):
            is_completed = self.instance.allocation.status == 'Completed'
            read_only_fields = [
                'assessment_type', 'assessment_name', 'assessment_date',
                'weightage', 'total_marks', 'student_submission',
                'submission_deadline', 'assessmentchecked_set',
            ]
            for field in read_only_fields:
                extra_kwargs[field] = {'read_only': is_completed}
            extra_kwargs['allocation'] = {'read_only': True}

        return extra_kwargs



    def validate_submission_deadline(self, value):
        if not value:
            return self.instance.submission_deadline if (self.instance and self.instance.submission_deadline) else None

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

    def validate(self, data):
        allocation_id = self.context.get('allocation_id')
        errors = {}
        weightage = data.get('weightage')
        assessment_name = data.get('assessment_name')

        if self.instance and weightage == self.instance.weightage and assessment_name == self.instance.assessment_name:
            return data

        all_assessments = Assessment.objects.filter(allocation=allocation_id)

        if weightage is not None:
            if weightage < 1:
                errors['weightage'] = 'Weightage cannot be less than 1.'
            total_weightage = sum([
                each.weightage if not self.instance or each.assessment_id != self.instance.assessment_id else 0
                for each in all_assessments
            ])
            if total_weightage + weightage > 100:
                errors['weightage'] = f'Total weightage: {total_weightage + weightage}, Error: Total weightage cannot exceed 100 for allocation_id: {allocation_id}'

        if assessment_name is not None:
            same_assessment = all_assessments.filter(
                assessment_type=data.get('assessment_type'),
                assessment_name=assessment_name
            )
            if self.instance:
                same_assessment = same_assessment.exclude(assessment_id=self.instance.assessment_id)
            if same_assessment.exists():
                errors['assessment_name'] = f"Assessment {assessment_name} already exists for the allocation_id: {allocation_id}"

        if data.get('student_submission') is True and not data.get('submission_deadline'):
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


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assessmentchecked_set'].context.update(self.context)

        if not isinstance(self.instance, Assessment):
            self.fields.pop('assessmentchecked_set')

        if self.instance and hasattr(self.instance, 'assessmentchecked_set'):
            self.fields['assessmentchecked_set'].instance = self.instance.assessmentchecked_set.all()

    def create(self, validated_data):
        validated_data['allocation'] = CourseAllocation.objects.get(allocation_id=self.context.get('allocation_id'))
        assessment = Assessment.objects.create(**validated_data)
        enrollment_set = get_list_or_404(Enrollment, allocation=assessment.allocation)
        for enrollment in enrollment_set:
            AssessmentChecked.objects.create(enrollment=enrollment, assessment=assessment)

        return assessment

    def update(self, instance, validated_data):
        if not validated_data.get('student_submission'):
            validated_data.pop('student_submission',{})
            validated_data.pop('submission_deadline',{})

        if 'assessmentchecked_set' in validated_data:
            assessmentChecked_data = validated_data.pop('assessmentchecked_set',{})
            if assessmentChecked_data and instance.assessmentchecked_set.exists():
                for each in instance.assessmentchecked_set.all():
                    data = next(
                        (item for item in assessmentChecked_data if item["enrollment"] == each.enrollment), None)
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
            'lecture',
            'enrollment',
            'is_present',
            'student_info'
        ]
        list_serializer_class = CustomizedListSerializer
        extra_kwargs = {'lecture': {'read_only': True}}

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
                'image': request.build_absolute_uri(obj.enrollment.student.student_id.image.url) if obj.enrollment.student.student_id.image else None,
                'student_id': obj.enrollment.student.student_id.person_id,
                'first_name': obj.enrollment.student.student_id.first_name,
                'last_name': obj.enrollment.student.student_id.last_name,
            }


    def validate(self, data):
        lecture = self.instance.lecture if self.instance else data.get('lecture')
        allocation = CourseAllocation.objects.filter(allocation_id=lecture.allocation.allocation_id).prefetch_related('enrollment_set')
        if not allocation.exists():
            raise serializers.ValidationError(f'No course allocations available for lecture: {lecture}')

        enrolled_students = allocation.first().enrollment_set.values_list('enrollment_id', flat=True)

        if not allocation.exists() or  data['enrollment'].pk not in enrolled_students :
            raise serializers.ValidationError(f'Student {data["enrollment"]} does not exist for course allocation: {allocation}')

        return data


class LectureHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.allocation is None:
            return None
        kwargs = {
            'allocation_id': obj.allocation.pk,
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
            'allocation',
            'starting_time',
            'venue',
            'duration',
            'topic',
            'attendance_set',
        ]
        extra_kwargs = {
            'lecture_id' : {'read_only': True},
            'lecture_no': {'read_only': True},
            'allocation': {'read_only': True},
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
        validated_data['allocation'] = CourseAllocation.objects.get(allocation_id=self.context.get('allocation_id'))
        lecture_count = Lecture.objects.filter(allocation=validated_data['allocation']).count()
        lecture_no = lecture_count +1
        validated_data['lecture_no'] = lecture_no

        logger.debug('Creating lecture with validated_data=%s', validated_data)
        attendance_set = {}

        if 'attendance_set' in validated_data:
            attendance_set = validated_data.pop('attendance_set', {})

        lecture = Lecture.objects.create(**validated_data)
        enrollments = get_list_or_404(Enrollment, allocation=validated_data['allocation'])

        if attendance_set:
            valid_enrollment_ids = {e.id for e in enrollments}
            for each in attendance_set:
                if each['enrollment'].id not in valid_enrollment_ids:
                    raise serializers.ValidationError("Enrollment does not belong to this allocation.")
                Attendance.objects.create(attendance_date=lecture.starting_time.date(), lecture=lecture, **each)

        else:
            for enrollment in enrollments:
                Attendance.objects.create(lecture=lecture, enrollment=enrollment)

        return lecture

    @transaction.atomic
    def update(self, instance, validated_data):
        attendance_set = validated_data.pop('attendance_set', {})

        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        instance.save()

        if attendance_set and instance.attendance_set.exists():
            for each in instance.attendance_set.all():
                data = next((item for item in attendance_set if item["enrollment"] == each.enrollment), None)
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
        if validated_data.get('status') in ['confirmed', 'pending']:
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

            enrollments = allocation.enrollment_set.all()
            Result.objects.bulk_create(
                [Result(enrollment=e) for e in enrollments.filter(result__isnull=True)],
                ignore_conflicts=True
            )
            calculated_result = enrollments.filter(
                result__course_gpa__isnull=False,
                result__obtained_marks__isnull=False
            ).count()

            if calculated_result > 1:
                instance.status = 'declined'
                instance.applied_at = timezone.now()
                instance.save()
                raise serializers.ValidationError('This results for this allocation have already been calculated')

            data = {}
            for each in allocation.assessment_set.all():
                for e in each.assessmentchecked_set.all():
                    if not e.obtained:
                        data[e.enrollment.student.student_id.person_id] = f'marks for assessment: {each.assessment_name} are null'

            if data:
                raise serializers.ValidationError(data)

            result_data = self.calculate_result(allocation)
            instance.status = 'applied'
            instance.applied_at = timezone.now()
            allocation.status = 'Completed'
            allocation.save()
            instance.save()

            return instance


