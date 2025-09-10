from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.views import APIView
from AdminModule.serializers import StudentSerializer
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from Models.serializers import CompilerSerializer
from StudentModule.serializers import *
from .mixins import *


@method_decorator(cache_page(60), name='dispatch')
class StudentDashboardView(
    StudentPermissionMixin,
    APIView
):
    def get(self,request,*args,**kwargs):
        if request.user.groups.filter(name='Student').exists():
            student_data = {}
            student = Student.objects.filter(student_id__user=request.user).prefetch_related('enrollment_set').first()
            if student:
                student_data['student_id'] = student.student_id.person_id
                student_data['first_name'] = student.student_id.first_name
                student_data['last_name'] = student.student_id.last_name
                student_data['institutional_email'] = student.student_id.institutional_email
                student_data['class'] = f'{student.class_id.program_id.program_id}-{student.class_id.batch_year}'
                student_data['program'] = student.program_id.program_name
                student_data['department'] = student.program_id.department_id.department_name
                student_data['image'] = request.build_absolute_uri(student.student_id.image.url) if student.student_id.image else None
                student_data['total_enrollments'] = student.enrollment_set.count()
                student_data['active_enrollments'] = student.enrollment_set.filter(status='Active').count()
                student_data['completed_enrollments'] = student.enrollment_set.filter(status='Completed').count()
                return Response(data=student_data, status=status.HTTP_200_OK)
            return Response(data={'error': 'student not found'}, status=status.HTTP_404_NOT_FOUND)

        else:
            return Response(data={'error': 'Please provide a valid user'},status=status.HTTP_403_FORBIDDEN)


class StudentProfileView(
    StudentPermissionMixin,
    APIView
):
    serializer_class = StudentSerializer
    def get(self, request):
        if request.user.groups.filter(name='Student').exists():
            student = Student.objects.get(student_id__user=request.user)
            serializer = self.serializer_class(student, context={'request': request})
            return Response(data=serializer.data)
        else:
            return Response(data={'message': 'A valid user not provided'},status=404)

    def put(self, request):
        if request.user.groups.filter(name='Student').exists():
            student = Student.objects.get(student_id__user=request.user)
            serializer = self.serializer_class(student,data=request.data, context={'request': request})
            if serializer.is_valid():
                serializer.save()
                return Response(data=serializer.data)
            else:
                return Response(data=serializer.errors, status=400)
        return Response(data={'message': 'A valid user not provided'},status=404)





class StudentEnrollmentsListView(
    StudentEnrollmentPermissionMixin,
    generics.ListAPIView
):
    serializer_class = StudentEnrollmentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['status', 'allocation_id__course_code']

    def get_queryset(self):
        queryset = Enrollment.objects.filter(student_id__student_id__user=self.request.user).filter(allocation_id__semester_id__status__in=['Active','Completed'])
        if queryset.exists():
            return queryset
        return Enrollment.objects.none()


class StudentEnrollmentRetrieveView(
    StudentEnrollmentPermissionMixin,
    generics.RetrieveAPIView
):
    serializer_class = StudentEnrollmentSerializer
    lookup_field = 'enrollment_id'
    def get_queryset(self):
        queryset = Enrollment.objects.filter(student_id__student_id__user=self.request.user)
        if queryset.exists():
            return queryset
        return Enrollment.objects.none()



class StudentAssessmentUploadView(
    StudentAssessmentUploadPermissionMixin,
    generics.UpdateAPIView
):
    queryset = AssessmentChecked.objects.all()
    serializer_class = StudentAssessmentCheckedSerializer
    lookup_field = 'id'


class StudentAttendanceListAPIView(
    generics.ListAPIView
):

    serializer_class = StudentAttendanceSerializer
    def get_queryset(self):
        queryset = Enrollment.objects.filter(student_id__student_id__user=self.request.user)
        if queryset.exists():
            return queryset
        return Enrollment.objects.none()



class StudentEnrollmentCreateAPIView(
    StudentEnrollmentCreatePermissionMixin,
    APIView
):
    def get(self,request):
        student = Student.objects.get(student_id__user=self.request.user)
        if not student:
            return Response(data={'error': 'not a valid user'}, status=status.HTTP_403_FORBIDDEN)

        semester = Semester.objects.filter(semesterdetails__class_id=student.class_id, status='Inactive',
                                           session__isnull=False, activation_deadline__isnull=False).prefetch_related('courseallocation_set').first()

        if not semester or not semester.courseallocation_set.exists():
            raise PermissionDenied('No new session available')
        serializer = StudentEnrollmentCreateSerializerA(semester.courseallocation_set.all(), many=True, context={'request': request})
        return Response(data=serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        count = 0
        for each in request.data:
            serializer = StudentEnrollmentCreateSerializerB(data=each, context={'request': request})
            if serializer.is_valid():
                instance = serializer.save()
                if isinstance(instance, dict):
                    count += instance.get('count')

        return Response(data={'message': f'{count} courses enrolled successfully'}, status=status.HTTP_200_OK)






class StudentAttendanceRetrieveAPIView(
    generics.RetrieveAPIView
):
    serializer_class = StudentAttendanceSerializer
    lookup_field = 'enrollment_id'
    def get_queryset(self):
        queryset = Enrollment.objects.filter(student_id__student_id__user=self.request.user)
        if queryset.exists():
            return queryset
        return Enrollment.objects.none()


class ReviewListAPIView(
    ReviewsPermissionMixin,
    generics.ListAPIView
):
    serializer_class = ReviewsSerializer

    def get_queryset(self):
        student_id = self.kwargs.get('student_id')
        queryset = Reviews.objects.filter(enrollment_id__student_id__student_id=student_id)
        return queryset


class ReviewCreateAPIView(
    ReviewsPermissionMixin,
    generics.CreateAPIView
):
    serializer_class = ReviewsSerializer

    def get_queryset(self):
        enrollment_id = self.kwargs.get('enrollment_id')
        queryset = Reviews.objects.filter(enrollment_id=enrollment_id)
        return queryset

    def get_serializer_context(self):
        enrollment_id = self.kwargs.get('enrollment_id')
        context = super().get_serializer_context()
        context['enrollment_id'] = enrollment_id
        return context


class ReviewRetrieveUpdateDestroyAPIView(
    ReviewsPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):

    serializer_class = ReviewsSerializer
    lookup_field = 'review_id'
    def get_queryset(self):
        enrollment_id = self.kwargs['enrollment_id']
        queryset = Reviews.objects.filter(enrollment_id=enrollment_id)
        return queryset


class StudentCompilerAPIView (APIView):
    serializer_class = CompilerSerializer
    def get(self, request, *args, **kwargs):
        data = {
            'Available Compiler are': {
                'Python' : 'Python 3.13 Interpreter',
                'C / C++' : 'gcc and g++',
            }
        }
        return Response(data)
    def post(self, request, *args, **kwargs):
        if 'file' in request.data and request.data['file'] == '':
            return Response(data={'error': 'Please provide a file'}, status=400)

        data = request.data.copy()
        data['input_list'] = request.data.get('input_list')
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(instance.data)
        else:
            return Response(serializer.errors, status=400)