from http import HTTPStatus
from django.core.cache import cache
from django.shortcuts import reverse, get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework import generics
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiExample

from AdminModule.tasks import send_result_calculation_mail
from DjangoRESTProject_practice import settings
from AdminModule.serializers import FacultySerializer
from FacultyModule.serializers import *
from .mixins import *

@extend_schema(
    description=(
        "This endpoint provides **Faculty Dashboard Data**.\n\n"
        "- Returns the logged-in faculty member's profile information.\n"
        "- Provides statistics about their course allocations, "
        "including counts of active and completed allocations.\n"
        "- Shows the average success score for each completed course allocation."
    ),
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description='Faculty Dashboard data retrieved successfully',
            examples=[
                OpenApiExample(
                    'Success Example',
                    value={
                        "faculty": {
                            "employee_id": "NUM-DCS-2023-10",
                            "image": "https://domain.com/media/profile_images/faculty.png",
                            "first_name": "John",
                            "last_name": "Doe",
                            "institutional_email": "faculty@domain.com"
                        },
                        "course_allocation_count": 8,
                        "active_allocations": 3,
                        "completed_allocations": 5,
                        "allocation_average_success": {
                            "ALLOC-001": 85.5,
                            "ALLOC-002": 78.25
                        }
                    }
                )
            ]
        ),
        403: OpenApiResponse(
            description="Forbidden - Only faculty can access this endpoint",
            response=OpenApiTypes.OBJECT,
            examples=[
                OpenApiExample(
                    'Forbidden Example',
                    value={"detail": "You do not have permission to perform this action."}
                )
            ]
        ),
    }
)


class FacultyDashboardView(
    FacultyPermissionMixin,
    APIView
):
    def get(self,request):
        cache_key = f'faculty:dashboard:{request.user.username}'
        data = cache.get(cache_key)
        if data is not None:
            return Response(data, status=status.HTTP_200_OK)

        faculty = Faculty.objects.filter(employee_id__user=self.request.user).prefetch_related('courseallocation_set').first()
        faculty_data = {
            'employee_id': faculty.employee_id.person_id,
            'image' : request.build_absolute_uri(faculty.employee_id.image.url) if faculty.employee_id.image else None,
            'first_name': faculty.employee_id.first_name,
            'last_name': faculty.employee_id.last_name,
            'institutional_email': faculty.employee_id.institutional_email,
        }
        course_allocation_count = faculty.courseallocation_set.all().count()
        active_allocations = faculty.courseallocation_set.filter(status='Ongoing').count()
        completed_allocations = faculty.courseallocation_set.filter(status='Completed').count()
        allocation_average_success = {}
        for each in faculty.courseallocation_set.filter(status='Completed'):
            average = sum([e.result.obtained_marks for e in each.enrollment_set.all() if e.result.obtained_marks])/ each.enrollment_set.all().count()
            allocation_average_success[each.allocation_id] = average

        data = {
            'faculty': faculty_data,
            'course_allocation_count': course_allocation_count,
            'active_allocations': active_allocations,
            'completed_allocations': completed_allocations,
            'allocation_average_success': allocation_average_success,
        }
        cache.set(cache_key, data, timeout=60*5)
        return Response(data, status=status.HTTP_200_OK)



class FacultyProfileView(
    FacultyPermissionMixin,
    APIView
):
    serializer_class = FacultySerializer

    def get(self, request):
        cache_key = f'faculty:{request.user.username}'
        cached_data = cache.get(cache_key)

        # In production → skip DB query on cache hit
        if not settings.DEBUG and cached_data is not None:
            return Response(cached_data, status=status.HTTP_200_OK)

        faculty = Faculty.objects.filter(employee_id__user=request.user).first()
        if not faculty:
            return Response({'error': 'user not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(faculty, context={'request': request})
        if cached_data is None:
            cache.set(cache_key, serializer.data, timeout=60*5)

        return Response(serializer.data, status=status.HTTP_200_OK)
        

    def put(self, request):
        cache_key = f'faculty:{request.user.username}'
        faculty = get_object_or_404(Faculty, employee_id__user=self.request.user)
        serializer = self.serializer_class(faculty,data=request.data, context={'request': request})
        if serializer.is_valid():
            instance = serializer.save()
            cache.delete(cache_key)
            cache.set(cache_key, instance.data, timeout=60*5)

            return Response(data=instance.data, status=status.HTTP_200_OK)
        else:
            return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)




class FacultyCourseAllocationView(
    FacultyCourseAllocationPermissionMixin,
    generics.ListAPIView
):

    serializer_class = get_faculty_allocation_serializer()
    def get_queryset(self):
        queryset = CourseAllocation.objects.filter(teacher_id__employee_id__user=self.request.user, status__in=['Ongoing', 'Completed'])
        if queryset.exists():
                return queryset
        return CourseAllocation.objects.none()

    def list(self, request, *args, **kwargs):
        cache_key = f'faculty:{request.user.username}:allocations'
        data = cache.get(cache_key)
        if data is  None:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True, context={'request': request})
                cache.set(cache_key, serializer.data, timeout=60*5)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True, context={'request': request})
            cache.set(cache_key, serializer.data, timeout=60*5)
            return Response(serializer.data, status=status.HTTP_200_OK)

        else:
            query_params = self.request.query_params
            if query_params is None:
                return Response(data=data, status=status.HTTP_200_OK)
            for each in query_params:
                if each in self.filterset_fields:
                    value = self.request.query_params.get(each)
                    filtered_data = [datarow for datarow in data if datarow.get(each) == value]
                    data = filtered_data

            return Response(data=data, status=status.HTTP_200_OK)



    filter_backends = [DjangoFilterBackend,SearchFilter]
    filterset_fields = [ 'status', 'course_code', 'semester_id']
    search_fields = [ 'enrollment__student_id__student_id__first_name',
                     'course_code__course_code', ]


class FacultyCourseAllocationRetrieveView(
    FacultyCourseAllocationPermissionMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = CourseAllocation.objects.all()
    serializer_class = get_faculty_allocation_serializer()
    lookup_field = 'allocation_id'



class AssessmentListCreateAPIView(
    FacultyAssessmentPermissionMixin,
    generics.ListCreateAPIView
):

    serializer_class = AssessmentSerializer
    filter_backends = [DjangoFilterBackend,SearchFilter, OrderingFilter]
    filterset_fields = [
        'assessment_type','assessment_name', 'total_marks', 'student_submission'
    ]
    search_fields = [
        'assessment_name', 'assessment_type'
    ]


    def get_queryset(self):
        allocation_id = self.kwargs.get('allocation_id')
        queryset = Assessment.objects.filter(allocation_id=allocation_id)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['allocation_id'] = self.kwargs.get('allocation_id')
        return context

    def list(self, request, *args, **kwargs):
        allocation_id = self.kwargs.get('allocation_id')
        cache_key = f'faculty:{request.user.username}:{allocation_id}:assessments'
        data = cache.get(cache_key)
        if data is None:
            queryset = self.filter_queryset(self.get_queryset())

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                cache.set(cache_key, serializer.data, timeout=60*5)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            cache.set(cache_key, serializer.data, timeout=60*5)
            return Response(serializer.data, status=status.HTTP_200_OK)

        else:
            query_params = self.request.query_params
            if not query_params:
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(data)

                return Response(data=data, status=status.HTTP_200_OK)
            for each in query_params:
                if each in self.filterset_fields:
                    value = self.request.query_params.get(each)
                    filtered_data = [datarow for datarow in data if datarow.get(each)==value]
                    data = filtered_data

            page = self.paginate_queryset(data)
            if page is not None:
                return self.get_paginated_response(data)

            return Response(data=data, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        allocation_id = self.kwargs.get('allocation_id')
        cache_key = f'faculty:{self.request.user.username}:{allocation_id}:assessments'
        instance = serializer.save()
        data = cache.get(cache_key) or []
        new_data = self.get_serializer(instance).data
        data.append(new_data)
        cache.set(cache_key, data, timeout=60*5)



class AssessmentRetrieveUpdateDestroyAPIView(
    FacultyAssessmentPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = AssessmentSerializer
    lookup_field = 'assessment_id'

    def get_queryset(self):
        allocation_id = self.kwargs.get('allocation_id')
        queryset = Assessment.objects.filter(allocation_id=allocation_id)
        return queryset


    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.assessmentchecked_set.all().delete()
        instance.delete()
        return Response(status=HTTPStatus.NO_CONTENT)




class LectureListCreateAPIView(
    FacultyLecturePermissionMixin,
    generics.ListCreateAPIView
):
    serializer_class = LectureSerializer
    def get_queryset(self):
        allocation_id = self.kwargs.get('allocation_id')
        queryset = Lecture.objects.filter(allocation_id=allocation_id)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['allocation_id'] = self.kwargs.get('allocation_id')
        return context



class LectureRetrieveUpdateDestroyAPIView(
    FacultyLecturePermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = LectureSerializer
    lookup_field = 'lecture_id'

    def get_queryset(self):
        allocation_id = self.kwargs.get('allocation_id')
        queryset = Lecture.objects.filter(allocation_id=allocation_id)
        return queryset



class ResultCalculationRequest(
    FacultyPermissionMixin,
    APIView
):
    def get(self, request, *args, **kwargs):
        if self.request.user.groups.filter(name='Faculty').exists():
            allocation_id = self.kwargs.get('allocation_id')
            allocation = CourseAllocation.objects.get(allocation_id=allocation_id)
            if not allocation.teacher_id.employee_id.user == self.request.user:
                return Response(data={'message':'You are not allowed to perform this action'},status=status.HTTP_403_FORBIDDEN)

            queryset = ChangeRequest.objects.filter(target_allocation=allocation)

            if queryset.filter(status='pending').exists():
                return Response(data={'message':'There is already a pending request'},status=status.HTTP_200_OK)
            if queryset.filter(status='confirmed').exists():
                return Response(data={'message':'The existing request has been approved, visit your portal to apply changes'},status=status.HTTP_200_OK)


            change_request = ChangeRequest.objects.create(
                change_type='result_calculation',
                target_allocation=allocation,
                requested_by=request.user,
                requested_at=timezone.now(),
            )
            confirmation_link = self.request.build_absolute_uri(
                reverse('Admin:confirm-change-request', args=[change_request.confirmation_token])
            )

            admin = Admin.objects.get(status='Active')
            if not admin:
                return Response(data={'message': 'Action not possible'}, status=status.HTTP_403_FORBIDDEN)

            send_result_calculation_mail.apply_aysnc(args=[change_request.pk, confirmation_link, admin.employee_id.institutional_email], eta=timezone.now()+timedelta(minutes=2))

            return Response(data={'message': 'The request has been successfully sent to the admin'})
        return Response(data={'message': 'A valid user not provided'}, status=status.HTTP_403_FORBIDDEN)



class FacultyRequestsListView(
    FacultyRequestPermissionMixin,
    generics.ListAPIView
):
    serializer_class = FacultyRequestsSerializer
    def get_queryset(self):
        queryset = ChangeRequest.objects.filter(requested_by=self.request.user)
        if queryset.exists():
            return queryset
        return ChangeRequest.objects.none()


class FacultyRequestsUpdateView(
    FacultyRequestPermissionMixin,
    generics.UpdateAPIView
):
    serializer_class = FacultyRequestsSerializer
    queryset = ChangeRequest.objects.all()
    lookup_field = 'pk'
    def get_queryset(self):
        queryset = ChangeRequest.objects.filter(requested_by=self.request.user)
        if queryset.exists():
            return queryset
        return ChangeRequest.objects.none()