from rest_framework import  serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from Models.models import *




class ReviewHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.review_id is None:
            return None
        kwargs = {
            'student_id': obj.enrollment_id.student_id,
            'enrollment_id' : obj.enrollment_id_id,
            'review_id': getattr(obj, self.lookup_field)
        }
        return self.reverse (view_name, kwargs=kwargs, request=request, format=format)



class ReviewsSerializer(serializers.ModelSerializer):
    urls = ReviewHyperlinkedIdentityField(
        view_name='review-detail',
        lookup_field='review_id',
    )
    class Meta:
        model = Reviews
        fields = [
            'urls',
            'review_id',
            'enrollment_id',
            'review_text',
            'rating',
            'create_date',
        ]
        extra_kwargs = {
            'review_id' : {'read_only': True},
            'enrollment_id': {'read_only': True},
            'create_date' : {'read_only': True},
        }

    def create(self, validated_data):
        enrollment = Enrollment.objects.get(enrollment_id=self.context.get('enrollment_id'))
        review = Reviews.objects.create(review_text=validated_data.get('review_text'),
                                        rating=validated_data.get('rating'),
                                        enrollment_id=enrollment)

        return review





class AssessmentCheckedHyperlinkedIdentityField(serializers.HyperlinkedIdentityField):
    def get_url(self, obj, view_name, request, format):
        if obj.assessment_id is None:
            return None
        kwargs = {
            'enrollment_id' : obj.enrollment_id.pk,
            'assessment_id': obj.assessment_id.pk,
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
            'assessment_id',
            'enrollment_id',
            'obtained',
            'student_upload'
        ]
        extra_kwargs = {
            'assessment_id': {'read_only': True},
            'enrollment_id': {'read_only': True},
            'obtained': {'read_only': True},
        }



    def get_extra_kwargs(self):
        extra_kwargs = super().get_extra_kwargs()
        if self.instance and (self.context.get('request').method == 'PUT' or self.context.get('request').method == 'PATCH'):
            if (self.instance.assessment_id.student_submission == True and self.instance.assessment_id.submission_deadline is not None and (self.instance.assessment_id.submission_deadline < timezone.now())) or self.instance.enrollment_id.status == 'Completed':
                    extra_kwargs = {
                        'student_upload' : {'read_only': True},
                    }
        return extra_kwargs


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance:
            if not self.instance.assessment_id.student_submission or (self.instance.assessment_id.student_submission == True and self.instance.assessment_id.submission_deadline < timezone.now()) or self.instance.enrollment_id.status == 'Completed':
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
            'file_upload',
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
                    assessment_id=instance.assessment_id,
                    enrollment_id__student_id__student_id__user=request.user
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
            'file_upload',
            'assessment_set'
        ]



    def get_faculty_details(self, obj):
        if obj.teacher_id:
            return {
                'teacher_id' : obj.teacher_id.employee_id.person_id,
                'teacher_first_name' : obj.teacher_id.employee_id.first_name,
                'teacher_last_name' : obj.teacher_id.employee_id.last_name,
            }
        return {}
    def get_course_details(self, obj):
        if obj.course_code:
            return {
                'course_code' : obj.course_code.course_code,
                'course_name' : obj.course_code.course_name,
                'credit_hours' : obj.course_code.credit_hours,
                'lab' : obj.course_code.lab,
                'pre_requisite' : obj.course_code.pre_requisite.course_code if obj.course_code.pre_requisite else None,
            }


class StudentEnrollmentSerializer(serializers.ModelSerializer):
    allocation_details = StudentCourseAllocationSerializer(
        source='allocation_id', read_only=True
    )
    url = serializers.HyperlinkedIdentityField(
        view_name='Student:enrollment-detail',
        lookup_field='enrollment_id',
    )
    class Meta:
        model = Enrollment
        fields = [
            'url',
            'enrollment_id',
            'student_id',
            'allocation_id',
            'status',
            'allocation_details'
        ]


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = [
            'lecture_id',
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

    def get_faculty_details(self, obj):
        if obj:
            return {
                'faculty_id' : obj.allocation_id.teacher_id.employee_id.person_id,
                'first_name' : obj.allocation_id.teacher_id.employee_id.first_name,
                'last_name' : obj.allocation_id.teacher_id.employee_id.last_name,
            }
        return None
    def get_course_details(self, obj):
        if obj:
            return {
                'course_code' : obj.allocation_id.course_code.course_code,
                'course_name' : obj.allocation_id.course_code.course_name,
                'credit_hours' : obj.allocation_id.course_code.credit_hours,
            }
        return None

    def get_attendance_details(self, obj):
        if obj:
            attendance = Attendance.objects.filter(student_id=obj.student_id, lecture_id__allocation_id=obj.allocation_id)
            return AttendanceSerializer(attendance, many=True).data
        return None

    def get_percentage(self, obj):
        print(obj)
        if obj:
            attendance = Attendance.objects.filter(student_id=obj.student_id, lecture_id__allocation_id=obj.allocation_id)
            total = attendance.count()
            attended = attendance.filter(is_present=True).count()
            return round((attended / total) * 100) if total else 0
        return None


class StudentEnrollmentCreateSerializerA(serializers.ModelSerializer):
    faculty_data = serializers.SerializerMethodField(read_only=True)
    course_data = serializers.SerializerMethodField(read_only=True)
    confirm = serializers.BooleanField(read_only=True)

    class Meta:
        model = CourseAllocation
        fields = [
            'allocation_id',
            'faculty_data',
            'course_data',
            'confirm',
        ]

    def get_faculty_data(self, obj):
        if obj:
            return {
                'faculty_id': obj.teacher_id.employee_id.person_id,
                'faculty_name': f"{obj.teacher_id.employee_id.first_name} {obj.teacher_id.employee_id.last_name}",
            }
        return None

    def get_course_data(self, obj):
        if obj:
            return {
                'course_code': obj.course_code.course_code,
                'course_name': obj.course_code.course_name,
                'credit_hours': obj.course_code.credit_hours,
                'lab': obj.course_code.lab,
            }
        return None

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        request = self.context.get('request')

        student = Student.objects.get(student_id__user=request.user)
        enrollment = Enrollment.objects.filter(allocation_id=instance, student_id=student, status='Inactive').first()

        representation['confirm'] = True if enrollment else False

        return representation


class StudentEnrollmentCreateSerializerB(serializers.Serializer):
    allocation_id = serializers.IntegerField()
    confirm = serializers.BooleanField()
    class Meta:
        fields = [
            'allocation_id',
            'confirm',
        ]

    def create(self, validated_data):
        print(validated_data)
        if not validated_data:
            return None
        request = self.context.get('request')
        count = 0
        if request:
            student = Student.objects.get(student_id__user=request.user)

            semester = Semester.objects.filter(semesterdetails__class_id=student.class_id, status='Inactive',
                                           session__isnull=False, activation_deadline__isnull=False).prefetch_related('courseallocation_set').first()

            allocation_ids = semester.courseallocation_set.all().values_list('allocation_id', flat=True)
            if validated_data['allocation_id'] in allocation_ids and validated_data['confirm']==True:
                count += 1
                enrollment = Enrollment.objects.filter(allocation_id=validated_data['allocation_id'], student_id=student).first()
                if not enrollment:
                    Enrollment.objects.create(allocation_id=semester.courseallocation_set.filter(allocation_id=validated_data['allocation_id']).first(), student_id=student)

            elif validated_data['allocation_id'] in allocation_ids and validated_data['confirm']==False:
                enrollment = Enrollment.objects.filter(allocation_id=validated_data['allocation_id'],student_id=student).first()
                if enrollment:
                    enrollment.delete()
            return {'count': count}