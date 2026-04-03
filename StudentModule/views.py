from http.client import responses

from django.core.cache import cache
from django.db.models.query import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.views import APIView
from AdminModule.serializers import StudentSerializer

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiExample

from Compilers.serializers import CompilerSerializer
from StudentModule.serializers import *
from .mixins import *


@extend_schema(
    description=(
        "This endpoint provides the **Student Dashboard Data**.\n\n"
        "It returns the authenticated student's profile details along with enrollment statistics.\n"
        "Only users with the **Student role** can access this endpoint."
    ),
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Student Dashboard data retrieved successfully",
            examples=[
                OpenApiExample(
                    'Success Response Example',
                    value={
                        "student_id": "NUM-STU-2024-01",
                        "first_name": "John",
                        "last_name": "Doe",
                        "institutional_email": "john.doe@domain.com",
                        "class": "BSCS-2024",
                        "program": "BS Computer Science",
                        "department": "Computer Science",
                        "image": "https://domain.com/media/profile_images/student.png",
                        "total_enrollments": 10,
                        "active_enrollments": 3,
                        "completed_enrollments": 7
                    }
                )
            ]
        ),
        403: OpenApiResponse(
            description="Forbidden - Only Student users can access this endpoint",
            response=OpenApiTypes.OBJECT,
            examples=[
                OpenApiExample(
                    'Forbidden Example',
                    value={"detail": "You do not have permission to perform this action."}
                )
            ]
        )
    }
)


class StudentDashboardView(
    StudentPermissionMixin,
    APIView
):
    def get(self,request,*args,**kwargs):
        cache_key = f'student:dashboard:{request.user.username}'
        data = cache.get(cache_key)
        if data is not None:
            return Response(data, status=status.HTTP_200_OK)

        student_data = {}
        student = Student.objects.filter(student_id__user=request.user).prefetch_related('enrollment_set').first()
        student_data['student_id'] = student.student_id.person_id
        student_data['first_name'] = student.student_id.first_name
        student_data['last_name'] = student.student_id.last_name
        student_data['institutional_email'] = student.student_id.institutional_email
        student_data['class'] = f'{student.student_class.program.program_id}-{student.student_class.batch_year}'
        student_data['program'] = student.program.program_name
        student_data['department'] = student.program.department.department_name
        student_data['image'] = request.build_absolute_uri(student.student_id.image.url) if student.student_id.image else None
        student_data['total_enrollments'] = student.enrollment_set.count()
        student_data['active_enrollments'] = student.enrollment_set.filter(status='Active').count()
        student_data['completed_enrollments'] = student.enrollment_set.filter(status='Completed').count()

        cache.set(cache_key, student_data, timeout=60*5)
        return Response(data=student_data, status=status.HTTP_200_OK)


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
    filterset_fields = ['status', 'allocation__course__course_code']

    def get_queryset(self):
        return Enrollment.objects.filter(
            student__student_id__user=self.request.user,
            allocation__semester__status__in=['Active', 'Completed']
        )


class StudentEnrollmentRetrieveView(
    StudentEnrollmentPermissionMixin,
    generics.RetrieveAPIView
):
    serializer_class = StudentEnrollmentSerializer
    lookup_field = 'enrollment_id'
    def get_queryset(self):
        return Enrollment.objects.filter(student__student_id__user=self.request.user)




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
        return Enrollment.objects.filter(student__student_id__user=self.request.user)



class StudentAttendanceRetrieveAPIView(
    generics.RetrieveAPIView
):
    serializer_class = StudentAttendanceSerializer
    lookup_field = 'enrollment_id'
    def get_queryset(self):
        return Enrollment.objects.filter(student__student_id__user=self.request.user)


class StudentEnrollmentCreateAPIView(
    StudentEnrollmentCreatePermissionMixin,
    APIView
):
    def get(self,request):
        student = request.student
        student_cache_key = f'enrollments:{student.student_id}:{student.student_class_id}:data'
        student_data = cache.get(student_cache_key)
        if student_data:
            return Response(data=student_data, status=status.HTTP_200_OK)

        cache_key = f'enrollments:{student.student_class_id}:semester:allocations'
        allocation_data = cache.get(cache_key)
        enrolled_allocations = set(Enrollment.objects.filter(student=student, status='Inactive').values_list('allocation_id', flat=True))
        for each in allocation_data:
            each['confirm'] = each['allocation_id'] in enrolled_allocations

        cache.set(student_cache_key, allocation_data, None)

        return Response(data=allocation_data, status=status.HTTP_200_OK)

    def post(self, request):
        request_data = request.data if isinstance(request.data, list) else [request.data]
        count = 0
        student_cache_data = []
        student = request.student
        cache_key = f'enrollments:{student.student_class_id}:semester:allocations'
        data = cache.get(cache_key)
        allocation_ids = {each['allocation_id'] for each in data}

        enrolled_allocations_id = set(Enrollment.objects.filter(student=student, status='Inactive').values_list('allocation_id', flat=True))
        for each in request_data:
            serializer = StudentEnrollmentCreateSerializerB(
                data=each,
                context={'request': request, 'allocation_ids': allocation_ids, 'enrolled_allocations_ids': enrolled_allocations_id}
            )
            if serializer.is_valid():
                instance = serializer.save()
                if isinstance(instance, dict):
                    return_count = instance.get('count')
                    count += return_count if return_count!=-1 else 0
                    return_id = instance.get('return_id')
                    if return_id and return_count >= 0:
                        student_cache_data.append(each)

        student_cache_key = f'enrollments:{student.student_id}:{student.student_class_id}:data'
        cache.set(student_cache_key, student_cache_data, timeout=None)
        return Response(data={'message': f'{count} courses enrolled successfully'}, status=status.HTTP_201_CREATED)


class ReviewListAPIView(
    ReviewsPermissionMixin,
    generics.ListAPIView
):
    serializer_class = ReviewsSerializer

    def get_queryset(self):
        student_id = self.kwargs.get('student_id')
        queryset = Reviews.objects.filter(enrollment__student__student_id=student_id)
        return queryset


class ReviewCreateAPIView(
    ReviewsPermissionMixin,
    generics.CreateAPIView
):
    serializer_class = ReviewsSerializer

    def get_queryset(self):
        enrollment_id = self.kwargs.get('enrollment_id')
        queryset = Reviews.objects.filter(enrollment=enrollment_id)
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
        queryset = Reviews.objects.filter(enrollment=enrollment_id)
        return queryset


class StudentCompilerAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompilerSerializer
    def get(self, request, *args, **kwargs):
        data = {
            'Available Compiler are': {
                'Python' : 'Python 3.13 Interpreter',
                'C / C++' : 'gcc and g++',
            }
        }
        return Response(data=data, status=status.HTTP_200_OK)


    def post(self, request, *args, **kwargs):
        if 'file' in request.data and request.data['file'] == '':
            return Response(data={'error': 'Please provide a file'}, status=status.HTTP_400_BAD_REQUEST)

        data = request.data.copy()
        data['input_list'] = request.data.get('input_list')
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            return Response(instance.data)
        else:
            return Response(serializer.errors, status=400)