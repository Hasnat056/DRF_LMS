from django.db.models import Count
from django.db.models.functions import ExtractYear
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
import django_filters
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied


from .tasks import cache_faculty_data_task, cache_student_data_task, cache_programs_data_task, cache_courses_data_task, \
    cache_semester_data_task, cache_courseAllocation_data_task, cache_enrollment_data_task, \
    send_result_calculation_confirmation_mail
from .serializers import *
from .mixins import *

from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
    OpenApiExample,
    OpenApiTypes
)
from rest_framework.views import APIView
from rest_framework.response import Response


@extend_schema(
    description=(
        "This endpoint provides the **Admin Dashboard Data**.\n\n"
        "- Returns admin profile info, total counts for entities (students, faculty, etc.),\n"
        "- Aggregated data such as yearly admissions, enrollments, and department stats.\n"
        "- Accessible only to authenticated **admins**."
    ),
    responses={
        200: OpenApiResponse(
            description="Admin dashboard data retrieved successfully",
            response=OpenApiTypes.OBJECT,
            examples=[
                OpenApiExample(
                    'Success Example',
                    value={
                        "admin": {
                            "admin_id": "NUM-ADM-2022-01",
                            "first_name": "Admin",
                            "last_name": "Admin",
                            "institutional_email": "admin@domain.com",
                            "image": "https://domain.com/media/profile_images/admin.png"
                        },
                        "students_total": 350,
                        "faculty_total": 20,
                        "programs_total": 5,
                        "courses_total": 45,
                        "classes_total": 12,
                        "allocation_total": 40,
                        "enrollment_total": 220,
                        "students_status_count": [
                            {"status": "Active", "count": 300},
                            {"status": "Inactive", "count": 50}
                        ],
                        "allocations_status_count": [
                            {"status": "Assigned", "count": 30},
                            {"status": "Pending", "count": 10}
                        ],
                        "enrollments_status_count": [
                            {"status": "Ongoing", "count": 180},
                            {"status": "Completed", "count": 40}
                        ],
                        "classes_student_count": [
                            {"class_id": 1, "count": 40},
                            {"class_id": 2, "count": 35}
                        ],
                        "departments_data": [
                            {
                                "department_id": 1,
                                "student_count": 120,
                                "faculty_count": 10,
                                "program_count": 3
                            },
                            {
                                "department_id": 2,
                                "student_count": 230,
                                "faculty_count": 12,
                                "program_count": 2
                            }
                        ],
                        "enrollment_yearly": [
                            {"year": 2022, "count": 100},
                            {"year": 2023, "count": 120}
                        ],
                        "yearly_admission": [
                            {
                                "program_id__department_id__department_name": "Computer Science",
                                "year": 2023,
                                "count": 80
                            },
                            {
                                "program_id__department_id__department_name": "Electrical Engineering",
                                "year": 2023,
                                "count": 40
                            }
                        ]
                    }
                )
            ]
        ),
        403: OpenApiResponse(
            description="Forbidden - Only admins can access this endpoint",
            response=OpenApiTypes.OBJECT,
            examples=[
                OpenApiExample(
                    'Forbidden Example',
                    value={"detail": "You do not have permission to perform this action."}
                )
            ]
        )
    },

)


class AdminDashboardAPIView(
    AdminPermissionMixin,
    APIView
):

    def get(self, request, *args, **kwargs):
        cache_key = f'admin:dashboard:{request.user.username}'
        data = cache.get(cache_key)
        if data is not None:
            return Response(data, status=status.HTTP_200_OK)

        admin = get_object_or_404(Admin, employee_id__user=request.user)
        admin_data = {
            'admin_id': admin.employee_id.person_id,
            'first_name': admin.employee_id.first_name,
            'last_name': admin.employee_id.last_name,
            'institutional_email': admin.employee_id.institutional_email,
            'image': request.build_absolute_uri(admin.employee_id.image.url) if admin.employee_id.image else None,
        }

        students_total = Student.objects.count()
        faculty_total = Faculty.objects.count()
        programs_total = Program.objects.count()
        courses_total = Course.objects.count()
        classes_total = Class.objects.count()
        allocation_total = CourseAllocation.objects.count()
        enrollment_total = Enrollment.objects.count()

        students_status_count = list((
            Student.objects.values('status')
            .annotate(count=Count('student_id'))
        ))

        allocations_status_count = list((
            CourseAllocation.objects.values('status')
            .annotate(count=Count('allocation_id'))
        ))

        enrollments_status_count = list((
            Enrollment.objects.values('status')
            .annotate(count=Count('enrollment_id'))
        ))

        classes_student_count = list((
            Class.objects.values('class_id')
            .annotate(count=Count('student'))
        ))

        departments_data = list((
            Department.objects
            .annotate(
                student_count=Count('program__student', distinct=True),
                faculty_count=Count('faculty', distinct=True),
                program_count=Count('program', distinct=True),
                enrollment_count=Count('program__student__enrollment', distinct=True),
            )
            .values('department_id', 'student_count', 'faculty_count', 'program_count')
        ))

        enrollment_yearly = list((
            Enrollment.objects.annotate(year=ExtractYear('enrollment_date'))
            .values('year')
            .annotate(count=Count('enrollment_id'))
        ))

        yearly_admission = list((
            Student.objects
            .annotate(year=ExtractYear('admission_date'))
            .values('program_id__department_id__department_name', 'year')
            .annotate(count=Count('student_id'))
            .order_by('program_id__department_id__department_name', 'year')
        ))

        data = {
            'admin': admin_data,
            'students_total': students_total,
            'faculty_total': faculty_total,
            'programs_total': programs_total,
            'courses_total': courses_total,
            'classes_total': classes_total,
            'enrollment_total': enrollment_total,
            'allocation_total': allocation_total,
            'students_status_count': students_status_count,
            'enrollments_status_count': enrollments_status_count,
            'allocations_status_count': allocations_status_count,
            'classes_student_count': classes_student_count,
            'departments_data': departments_data,
            'enrollment_yearly': enrollment_yearly,
            'yearly_admission': yearly_admission,
        }
        cache.set(cache_key,data, timeout=60*5)

        return Response(data)




class AdminProfileAPIView(
    AdminPermissionMixin,
    APIView
):
    serializer_class = AdminSerializer

    def get(self, request, *args, **kwargs):
        cache_key = f'admin:{request.user.username}'
        cached_data = cache.get(cache_key)

        # In production → skip DB query on cache hit
        if not settings.DEBUG and cached_data is not None:
            return Response(cached_data, status=status.HTTP_200_OK)

        # In development → allow browsable API to pre-fill form
        admin_instance = Admin.objects.filter(employee_id__user=request.user).first()
        if not admin_instance:
            return Response({'error': 'user not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(admin_instance, context={'request': request})

        if cached_data is None:  # only cache if not cached yet
            cache.set(cache_key, serializer.data, timeout=60 * 60 * 24)

        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self,request,*args,**kwargs):
        cache_key = f'admin:{request.user.username}'
        admin = Admin.objects.get(employee_id__user=request.user)
        serializer = self.serializer_class(admin, data=request.data, context={'request':request})
        if serializer.is_valid():
            instance = serializer.save()
            cache.delete(cache_key)
            cache.set(cache_key,instance.data,timeout=60*60*24)
            return Response(instance.data,status=status.HTTP_200_OK)

        return Response(serializer.errors,status=status.HTTP_400_BAD_REQUEST)





class FacultyListCreateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.ListCreateAPIView
):
    queryset = Faculty.objects.all()
    serializer_class = FacultySerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['department_id', 'designation']
    search_fields = ['employee_id__first_name', 'employee_id__last_name', 'employee_id__institutional_email']

    def list(self, request, *args, **kwargs):

        cache_key = f'admin:faculty_list'
        cached_data = cache.get(cache_key)
        # if cached data is not available
        if cached_data is None:
            cache_faculty_data_task.delay(request.user.id)
            return super().list(request, *args, **kwargs)

        else:
            query_params = request.query_params

            filter_params = {
                key : value for key, value in query_params.items() if key!='page'
            }
            if not query_params or not filter_params:
                page = self.paginate_queryset(cached_data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(cached_data, status=status.HTTP_200_OK)

            # if there are search and ordering filters fall back to DjangoFilterBackend
            if 'search' in query_params or 'ordering' in filter_params:
                return super().list(request, *args, **kwargs)

            if 'department_id' in filter_params and 'designation' in filter_params and len(filter_params)==2:
                cache_key = f'admin:faculty:{filter_params.get("department_id")}:{filter_params.get("designation")}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                page = self.paginate_queryset(data)
                if page is None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            # if applied filter is of department
            if 'department_id' in filter_params and len(filter_params)==1:
                value = query_params.get('department_id')
                cache_key = f'admin:faculty:department:{value}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            # if applied filter is of designation
            if 'designation' in filter_params and len(filter_params)==1:
                value = query_params.get('designation')
                cache_key = f'admin:faculty:designation:{value}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)


        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()
        cache_faculty_data_task.delay(self.request.user.id)







class FacultyRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = Faculty.objects.all()
    serializer_class = FacultySerializer
    lookup_field = 'employee_id'
    change_type = 'faculty_delete'
    target_field_name = 'target_faculty'


    def perform_update(self, serializer):
        serializer.save()
        cache_faculty_data_task.delay(self.request.user.id)

    def destroy(self, request, *args, **kwargs):
       return self.destroy_mixin()




class StudentListCreateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.ListCreateAPIView
):
    queryset = Student.objects.all()
    serializer_class = StudentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['program_id', 'class_id', 'program_id__department_id','status']
    search_fields = ['student_id__first_name', 'student_id__last_name', 'student_id__institutional_email']

    def list(self, request, *args, **kwargs):
        cache_key = 'admin:student_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            #print('Cache Miss')
            cache_student_data_task.delay(request.user.id)
            return super().list(request, *args, **kwargs)

        else:
            query_params = request.query_params
            #print(query_params)
            filter_params = {
                key : value for key,value in query_params.items() if key!='page' and value !=''
            }
            #print(filter_params)
            if not query_params or not  filter_params:
                print('Cache Hit')
                page = self.paginate_queryset(cached_data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(cached_data, status=status.HTTP_200_OK)

            if 'search' in query_params or 'ordering' in filter_params or len(filter_params)>2:
                print('Cache Miss')
                return super().list(request, *args, **kwargs)

            if len(filter_params)==2:
                if 'program_id__department_id' in filter_params and 'status' in filter_params:
                    cache_key = f'admin:students{query_params.get("program_id__department_id")}:{query_params.get("status")}'
                    data = cache.get(cache_key)
                    if data is None:
                        return super().list(request, *args, **kwargs)
                    print('Cache Hit')
                    page = self.paginate_queryset(data)
                    if page is not None:
                        return self.get_paginated_response(page)
                    return Response(data, status=status.HTTP_200_OK)
                else:
                    return super().list(request, *args, **kwargs)

            if 'program_id' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:program:{query_params.get("program_id")}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                print('Cache Hit')
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'program_id__department_id' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:department:{query_params.get("program_id__department_id")}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                print('Cache Hit')
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'class_id' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:class:{query_params.get("class_id")}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                print('Cache Hit')
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'status' in filter_params and len(filter_params)==1:
                cache_key = f'admin:student_list:status:{query_params.get("status")}'
                data = cache.get(cache_key)
                if data is None:
                    return super().list(request, *args, **kwargs)
                print('Cache Hit')
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)


    def perform_create(self, serializer):
        serializer.save()
        cache_student_data_task.delay(self.request.user.id)





class StudentRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = Student.objects.all()
    serializer_class = StudentSerializer
    lookup_field = 'student_id'
    change_type = 'student_delete'
    target_field_name = 'target_student'

    def perform_update(self, serializer):
        serializer.save()
        cache_student_data_task.delay(self.request.user.id)

    def destroy(self, request, *args, **kwargs):
        return self.destroy_mixin()


class DepartmentListAPIView(
    DepartmentPermissionMixin,
    generics.ListAPIView
):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer

class DepartmentRetrieveUpdateAPIView(
    DepartmentPermissionMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    lookup_field = 'department_id'

class ProgramListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = Program.objects.all()
    serializer_class = ProgramSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['department_id', 'total_semesters']
    search_fields = ['program_id', 'program_name']

    def list(self, request, *args, **kwargs):
        cache_key = f'admin:program_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            cache_programs_data_task.delay(request.user.id)
            return super().list(request, *args, **kwargs)
        else:
            query_params = request.query_params
            filter_params = {
                key : value for key,value in query_params.items() if key!='page'
            }
            if not query_params or not filter_params:
                page = self.paginate_queryset(cached_data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(cached_data, status=status.HTTP_200_OK)

            if 'search' in query_params or 'ordering' in filter_params:
                return super().list(request, *args, **kwargs)

            if len(filter_params) == 1 and 'department_id' in filter_params:
                cache_key = f'admin:programs:department:{query_params.get("department_id")}'
                data = cache.get(cache_key)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()
        cache_programs_data_task.delay(self.request.user.id)


class ProgramRetrieveUpdateDestroyAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = Program.objects.all()
    serializer_class = ProgramSerializer
    lookup_field = 'program_id'

    def perform_update(self, serializer):
        serializer.save()
        cache_programs_data_task.delay(self.request.user.id)

    def perform_destroy(self, instance):
        instance.delete()
        cache_programs_data_task.delay(self.request.user.id)


class CourseFilter(django_filters.FilterSet):
    prefix = django_filters.ChoiceFilter(field_name='course_code', lookup_expr='startswith', choices=[])
    class Meta:
        model = Course
        fields = ['prefix', 'lab', 'pre_requisite']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        course_codes = Course.objects.values_list('course_code', flat=True)
        prefixes = sorted(set([each.split('-')[0] for each in course_codes]))
        self.filters['prefix'].extra['choices'] = [(p,p) for p in prefixes]


class CourseListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = CourseFilter
    search_fields = ['course_code', 'course_name', 'pre_requisite__course_code']

    def list(self, request, *args, **kwargs):
        cache_key = 'admin:courses_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            print('Cache Miss:1')
            cache_courses_data_task.delay(request.user.id)
            return super().list(request, *args, **kwargs)

        else:
            filter_params = {
                key : value for key, value in request.query_params.items() if key!= 'page'
            }
            if filter_params:
                print('Cache Miss:2')
                return super().list(request, *args, **kwargs)

            page = self.paginate_queryset(cached_data)
            print('Cache Hit')
            if page is not None:
                return self.get_paginated_response(page)
            return Response(cached_data, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        serializer.save()
        cache_courses_data_task.delay(self.request.user.id)



class CourseRetrieveUpdateDestroyAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    lookup_field = 'course_code'

    def perform_update(self, serializer):
        serializer.save()
        cache_courses_data_task.delay(self.request.user.id)

    def perform_destroy(self, instance):
        instance.delete()
        cache_courses_data_task.delay(self.request.user.id)



class SemesterListAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListAPIView
):
    queryset = Semester.objects.all()
    serializer_class = SemesterSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['semesterdetails__class_id']
    
    def list(self, request, *args, **kwargs):
        cache_key = 'admin:semesters_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            cache_semester_data_task.delay(self.request.user.id)
            return  super().list(request, *args, **kwargs)
        
        query_params = request.query_params
        filter_params = {
            key : value for key, value in query_params.items() if key!='page' and value != ''
        }
        
        if not query_params or not filter_params:
            print('Cache Hit')
            page = self.paginate_queryset(cached_data)
            if page is not None:
                return self.get_paginated_response(page)
            return Response(cached_data, status=status.HTTP_200_OK)
        
        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if 'semesterdetails__class_id' in filter_params:
            cache_key= f'admin:semesters:class:{filter_params.get('semesterdetails__class_id')}'
            data = cache.get(cache_key)
            if data is None:
                return super().list(request, *args, **kwargs)
            print('Cache Hit')
            page = self.paginate_queryset(data)
            if page is None:
                return self.get_paginated_response(page)
            return Response(data, status=status.HTTP_200_OK)
        
        return super().list(request, *args, **kwargs)

class SemesterRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = Semester.objects.all()
    serializer_class = SemesterSerializer
    lookup_field = 'semester_id'

    def perform_update(self, serializer):
        serializer.save()
        cache_semester_data_task.delay(self.request.user.id)


class ClassListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = Class.objects.all()
    serializer_class = ClassSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['program_id', 'program_id__department_id','batch_year']
    search_fields = ['program_id', 'batch_year']

    def perform_create(self, serializer):
        serializer.save()
        cache_semester_data_task.delay(self.request.user.id)


    


class ClassRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = Class.objects.all()
    serializer_class = ClassSerializer
    lookup_field = 'class_id'
    filter_backends = [OrderingFilter]
    ordering_fields = ['semesterdetails__semester_id__semester_no']


class CourseAllocationListCreateAPIView (
    AdminCourseAllocationPermissionMixin,
    generics.ListCreateAPIView
):
    queryset = CourseAllocation.objects.all()
    serializer_class = CourseAllocationSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['teacher_id', 'status', 'course_code', 'semester_id']
    search_fields = ['teacher_id__employee_id__person_id', 'teacher_id__employee_id__first_name',
                     'teacher_id__employee_id__last_name','enrollment__student_id__student_id__first_name',
                     'course_code__course_code', ]

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key : value for key,value in query_params.items() if key!='page' and value!=''
        }
        if not query_params or not filter_params:
            return super().list(request, *args, **kwargs)

        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if len(filter_params)==1 and ('semester_id' in filter_params or 'teacher_id' in filter_params):
            cache_key = f'admin:allocations:semester:{filter_params.get("semester_id")}' if 'semester_id' in filter_params else f'admin:allocations:faculty:{filter_params.get("teacher_id")}'
            data = cache.get(cache_key)
            if data is None:
                cache_courseAllocation_data_task.delay(self.request.user.id)
                return super().list(request, *args, **kwargs)
            page = self.paginate_queryset(data)
            if page is None:
                return self.get_paginated_response(page)
            return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()
        cache_courseAllocation_data_task.delay(self.request.user.id)



class CourseAllocationRetrieveUpdateDestroyAPIView(
    AdminCourseAllocationPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = CourseAllocation.objects.all()
    serializer_class = CourseAllocationSerializer
    lookup_field = 'allocation_id'

    def perform_update(self, serializer):
        serializer.save()
        cache_courseAllocation_data_task.delay(self.request.user.id)

    def perform_destroy(self, instance):
        instance.delete()
        cache_courseAllocation_data_task.delay(self.request.user.id)



class EnrollmentListCreateAPIView(
    AdminEnrollmentPermissionMixin,
    generics.ListCreateAPIView
):

    serializer_class = EnrollmentSerializer
    queryset = Enrollment.objects.all()

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['student_id','allocation_id__teacher_id',
                        'status', 'allocation_id__semester_id','result__course_gpa']

    search_fields = ['student_id__student_id__person_id', 'student_id__student_id__first_name',
                     'student_id__student_id__last_name']

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key: value for key, value in query_params.items() if key != 'page' and value != ''
        }
        if not query_params or not filter_params:
            return super().list(request, *args, **kwargs)

        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if len(filter_params) == 1 and ('student_id' in filter_params or 'allocation_id__teacher_id' in filter_params):
            cache_key = f'admin:enrollments:student:{filter_params.get("student_id")}' if 'student_id' in filter_params else f'admin:enrollments:faculty:{filter_params.get("allocation_id__teacher_id")}'
            data = cache.get(cache_key)
            if data is None:
                cache_enrollment_data_task.delay(self.request.user.id)
                return super().list(request, *args, **kwargs)
            page = self.paginate_queryset(data)
            if page is None:
                return self.get_paginated_response(page)
            return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()
        cache_enrollment_data_task.delay(self.request.user.id)



class EnrollmentRetrieveUpdateDestroyAPIView(
    AdminEnrollmentPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = Enrollment.objects.all()
    serializer_class = EnrollmentSerializer
    lookup_field = 'enrollment_id'

    def perform_update(self, serializer):
        serializer.save()
        cache_enrollment_data_task.delay(self.request.user.id)

    def perform_destroy(self, instance):
        result = Result.objects.get(enrollment_id=instance.enrollment_id)
        if result.course_gpa:
            raise PermissionDenied('This enrollment cannot be deleted')
        else:
            instance.delete()
            cache_enrollment_data_task.delay(self.request.user.id)



class TranscriptListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = Transcript.objects.all()
    serializer_class = TranscriptSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['semester_id', 'student_id']
    search_fields = ['student_id__student_id__person_id', 'student_id__student_id__first_name',
                     'student_id__student_id__last_name']

class TranscriptBulkCreateAPIView(
    IsSuperUserOrAdminMixin,
    APIView
):
    serializer_class = BulkTranscriptSerializer
    def post(self, request, *args, **kwargs):
        if self.request.user.is_superuser or self.request.user.groups.filter(name='Admin').exists():
            semester_id = kwargs.get('semester_id')
            serializer = self.serializer_class(data=request.data, context={'semester_id': semester_id})
            if serializer.is_valid():
                instance = serializer.save()
                return Response(instance.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_401_UNAUTHORIZED)





class ChangeRequestListAPIView(
    ChangeRequestPermissionMixin,
    generics.ListAPIView
):
    queryset = ChangeRequest.objects.all()
    serializer_class = ChangeRequestSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'change_type', 'target_faculty','target_student']


class ChangeRequestRetrieveUpdateAPIView(
    ChangeRequestPermissionMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = ChangeRequest.objects.all()
    serializer_class = ChangeRequestSerializer


@extend_schema(
    responses={
        200:OpenApiResponse(
            description="Request Confirmation Success",
            examples={
                OpenApiExample(
                    'Success Example',
                    value={'message' : 'Change Request confirmation successfully'},
                )
            }
        ),
        400:OpenApiResponse(
            description="Link expired or already processed",
        )
    }
)
class ChangeRequestView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token, *args, **kwargs):
        change_request = get_object_or_404(ChangeRequest, confirmation_token=token)

        expiry_time = change_request.requested_at + timedelta(hours=48)

        if timezone.now() > expiry_time:
            change_request.status = 'expired'
            change_request.save()

            return Response({"error": "This request has expired."}, status=400)

        if change_request.status != 'pending':
                return Response({"error": "This request has already been processed."}, status=400)

        change_request.status = 'confirmed'
        change_request.confirmed_at = datetime.now()
        change_request.save()
        if change_request.change_type == 'result_calculation':
            send_result_calculation_confirmation_mail.apply_aysnc(args=[change_request.pk],eta=timezone.now()+timedelta(minutes=2))


        return Response({"message": "Change request confirmed successfully!"},status=status.HTTP_200_OK)


class BulkCreateAPIView(
    IsSuperUserOrAdminMixin,
    APIView
):

    serializer_class = FacultyStudentBulkSerializer

    def post(self, request, *args, **kwargs):
        target_model = request.query_params.get('type')

        serializer = self.serializer_class(data=request.data, context={'request': request, 'target_model': target_model})
        if serializer.is_valid(raise_exception=True):
            result = serializer.save()
            return Response({"message" : result}, status=status.HTTP_201_CREATED)


    def get(self, request, *args, **kwargs):
        if not request.query_params.get('type'):
            return Response({"error": "Template type not specified"}, status=400)

        target_model = request.query_params.get('type')

        file_headers = ['password','person_id','image','first_name','last_name','father_name','gender','cnic','dob','contact_number','institutional_email','personal_email',
                          'religion','country','province','city','zipcode','street_address','degree_title_1','education_board_1','institution_1','passing_year_1',
                          'total_marks_1','obtained_marks_1','is_current_1','degree_title_2','education_board_2','institution_2','passing_year_2','total_marks_2','obtained_marks_2'
                         ,'is_current_2','degree_title_3','education_board_3','institution_3','passing_year_3',
                          'total_marks_3','obtained_marks_3','is_current_3','degree_title_4','education_board_4','institution_4','passing_year_4',
                          'total_marks_4','obtained_marks_4','is_current_4','degree_title_5','education_board_5','institution_5','passing_year_5',
                          'total_marks_5','obtained_marks_5','is_current_5']

        if target_model == 'faculty':
            file_headers.append('department_id',)
            file_headers.append('designation',)
            file_headers.append('joining_date',)

        if target_model == 'student':
            file_headers.append('program_id',)
            file_headers.append('class_id',)
            file_headers.append('admission_date',)


        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(file_headers)

        template = buffer.getvalue()
        buffer.close()

        return HttpResponse(template, content_type='text/csv',
                            headers={'Content-Disposition': 'attachment; filename=template.csv'})








