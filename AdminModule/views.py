import logging
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Prefetch, RestrictedError, Subquery
from django.db.models.functions import Coalesce, ExtractYear
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
import django_filters
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny


from .tasks import cache_faculty_data_task, cache_student_data_task, cache_programs_data_task, cache_courses_data_task, \
    cache_semester_data_task, cache_courseAllocation_data_task, cache_enrollment_data_task, \
    send_result_calculation_confirmation_mail, _semester_cache_queryset
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

logger = logging.getLogger(__name__)


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

        admin = Admin.objects.filter(employee_id__user=request.user).select_related('employee_id').first()

        if not admin:
            return Response(status=status.HTTP_404_NOT_FOUND)

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

        # Three independent one-to-many joins hung off one table: MySQL builds
        # their cartesian product — students x faculty x programs per
        # department — and COUNT(DISTINCT) then de-duplicates it. At 1,000
        # students and 40 faculty per department that is a 40,000-row
        # intermediate per department, for three individually trivial numbers.
        # It measured 7,179 ms of this view's 8,455 ms.
        #
        # A correlated subquery per relation counts each on its own index. The
        # fourth annotation, enrollment_count, is gone: it was never in the
        # values() output, so it was computed (over 75,000 rows) and discarded.
        def _per_department(queryset, path):
            return Coalesce(
                Subquery(
                    queryset.filter(**{path: OuterRef('pk')})
                    # order_by() strips any Meta ordering, which would
                    # otherwise be dragged into the GROUP BY.
                    .order_by()
                    .values(path)
                    .annotate(total=Count('pk'))
                    .values('total'),
                    output_field=IntegerField(),
                ),
                0,
            )

        departments_data = list((
            Department.objects
            .annotate(
                student_count=_per_department(Student.objects, 'program__department'),
                faculty_count=_per_department(Faculty.objects, 'department'),
                program_count=_per_department(Program.objects, 'department'),
            )
            # The old query's GROUP BY sorted the rows as a side effect. There
            # is no GROUP BY now, so the order has to be asked for, or the
            # dashboard payload silently changes order.
            .order_by('department_id')
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
            .values('program__department__department_name', 'year')
            .annotate(count=Count('student_id'))
            .order_by('program__department__department_name', 'year')
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
        # Nothing invalidates this key on a write, so the TTL is the only
        # thing bounding how stale the counts get.
        cache.set(cache_key,data, timeout=60)

        return Response(data)




class AdminProfileAPIView(
    AdminPermissionMixin,
    APIView
):
    serializer_class = AdminSerializer

    def get(self, request, *args, **kwargs):
        cache_key = f'admin:{request.user.username}'
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return Response(cached_data, status=status.HTTP_200_OK)

        admin_instance = Admin.objects.filter(employee_id__user=request.user).select_related('employee_id').first()
        if not admin_instance:
            return Response({'error': 'user not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(admin_instance, context={'request': request})

        cache.set(cache_key, serializer.data, timeout=60*60*12)

        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self,request,*args,**kwargs):
        cache_key = f'admin:{request.user.username}'
        admin = Admin.objects.get(employee_id__user=request.user)
        serializer = self.serializer_class(admin, data=request.data, context={'request':request})
        if serializer.is_valid():
            serializer.save()
            cache.delete(cache_key)
            cache.set(cache_key,serializer.data,timeout=60*60*12)
            return Response(serializer.data,status=status.HTTP_200_OK)

        return Response(serializer.errors,status=status.HTTP_400_BAD_REQUEST)





class FacultyListCreateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.ListCreateAPIView
):
    # The same prefetching cache_faculty_data_task already feeds this
    # serializer. Without it a bare page of 10 rows costs 45 queries: person,
    # user, address and qualifications, once per row. The ?search and
    # ?ordering paths never consult the cache, so they were the only paths not
    # getting it — the same fault as StudentListCreateAPIView below.
    queryset = Faculty.objects.select_related(
        'employee_id', 'employee_id__user', 'employee_id__address', 'department',
    ).prefetch_related('employee_id__qualification_set')
    serializer_class = FacultySerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['department', 'designation']
    search_fields = ['employee_id__first_name', 'employee_id__last_name', 'employee_id__institutional_email']

    @staticmethod
    def _groups_for(filter_params):
        """The one group this request needs, in the task's own terms.

        `cache_faculty_data_task` always rewrites `admin:faculty_list`, and
        with an empty group list it writes nothing else -- so [] means
        "rebuild only the unfiltered key". A tuple with one half None asks for
        just that half's key.
        """
        department = filter_params.get('department')
        designation = filter_params.get('designation')
        if department is None and designation is None:
            return []
        return [(department, designation)]

    def list(self, request, *args, **kwargs):

        query_params = request.query_params
        filter_params = {
            key : value for key, value in query_params.items() if key!='page'
        }

        cache_key = f'admin:faculty_list'
        cached_data = cache.get(cache_key)
        # if cached data is not available
        if cached_data is None:
            # Rebuild the unfiltered key plus whichever group this request
            # asked for -- not every department, designation and pair in the
            # system, which is what an unscoped call does.
            cache_faculty_data_task.delay(
                request.user.id, faculty_groups=self._groups_for(filter_params),
            )
            return super().list(request, *args, **kwargs)

        else:
            if not query_params or not filter_params:
                page = self.paginate_queryset(cached_data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(cached_data, status=status.HTTP_200_OK)

            # if there are search and ordering filters fall back to DjangoFilterBackend
            if 'search' in query_params or 'ordering' in filter_params:
                return super().list(request, *args, **kwargs)

            if 'department' in filter_params and 'designation' in filter_params and len(filter_params)==2:
                cache_key = f'admin:faculty:{filter_params.get("department")}:{filter_params.get("designation")}'
                data = cache.get(cache_key)
                if data is None:
                    # Only this pair is missing; rebuilding every other
                    # department and designation would not answer the request
                    # any sooner.
                    cache_faculty_data_task.delay(
                        request.user.id,
                        faculty_groups=self._groups_for(filter_params),
                    )
                    return super().list(request, *args, **kwargs)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            # if applied filter is of department
            if 'department' in filter_params and len(filter_params)==1:
                value = query_params.get('department')
                cache_key = f'admin:faculty:department:{value}'
                data = cache.get(cache_key)
                if data is None:
                    cache_faculty_data_task.delay(
                        request.user.id, faculty_groups=[(value, None)],
                    )
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
                    cache_faculty_data_task.delay(
                        request.user.id, faculty_groups=[(None, value)],
                    )
                    return super().list(request, *args, **kwargs)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)


        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        instance = serializer.save()
        cache_faculty_data_task.delay(
            self.request.user.id,
            faculty_groups=[(instance.department_id, instance.designation)],
        )







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
        previous = serializer.instance
        old_group = (previous.department_id, previous.designation)
        instance = serializer.save()
        cache_faculty_data_task.delay(
            self.request.user.id,
            faculty_groups=[old_group, (instance.department_id, instance.designation)],
        )

    def destroy(self, request, *args, **kwargs):
       return self.destroy_mixin()




class StudentListCreateAPIView(
    IsSuperUserOrAdminMixin,
    PersonSerializerMixin,
    generics.ListCreateAPIView
):
    # The same prefetching cache_student_data_task already feeds this
    # serializer. Without it a bare page of 10 rows costs 66 queries: 11 x
    # auth_user, 10 x person, 10 x address, 10 x qualification. The ?search
    # and ?ordering paths never touch the cache, so they were the only paths
    # not getting it — and they are the ones admins use most.
    queryset = Student.objects.select_related(
        'student_id', 'student_id__user', 'student_id__address', 'program',
        'student_class', 'student_class__program',
    ).prefetch_related('student_id__qualification_set')
    serializer_class = StudentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['program', 'student_class', 'program__department','status']
    search_fields = ['student_id__first_name', 'student_id__last_name', 'student_id__institutional_email']

    @staticmethod
    def _groups_for(filter_params):
        """The one group this request needs, in the task's own terms.

        cache_student_data_task always rewrites `admin:student_list`, and with
        an empty group list it writes nothing else -- so [] means "rebuild
        only the unfiltered key". The tuple is
        (department, program, class, status); None in a slot skips that key.
        """
        group = (
            filter_params.get('program__department'),
            filter_params.get('program'),
            filter_params.get('student_class'),
            filter_params.get('status'),
        )
        return [] if not any(group) else [group]

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key : value for key,value in query_params.items() if key!='page' and value !=''
        }

        cache_key = 'admin:student_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            # Rebuild the unfiltered key plus whichever group was asked for.
            # Unscoped this rebuilt 5 departments x (1 + 4 statuses) + 10
            # programs + 40 classes + 4 statuses over 5,000 students -- 18.8
            # of the 19.7 seconds of total worker fill measured in the audit.
            cache_student_data_task.delay(
                request.user.id, student_groups=self._groups_for(filter_params),
            )
            return super().list(request, *args, **kwargs)

        else:
            if not query_params or not  filter_params:
                logger.debug('Cache hit for %s', cache_key)
                page = self.paginate_queryset(cached_data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(cached_data, status=status.HTTP_200_OK)

            if 'search' in query_params or 'ordering' in filter_params or len(filter_params)>2:
                logger.debug('Cache miss for %s', cache_key)
                return super().list(request, *args, **kwargs)

            if len(filter_params)==2:
                if 'program__department' in filter_params and 'status' in filter_params:
                    cache_key = f'admin:students:{query_params.get("program__department")}:{query_params.get("status")}'
                    data = cache.get(cache_key)
                    if data is None:
                        cache_student_data_task.delay(
                            request.user.id, student_groups=self._groups_for(filter_params),
                        )
                        return super().list(request, *args, **kwargs)
                    logger.debug('Cache hit for %s', cache_key)
                    page = self.paginate_queryset(data)
                    if page is not None:
                        return self.get_paginated_response(page)
                    return Response(data, status=status.HTTP_200_OK)
                else:
                    return super().list(request, *args, **kwargs)

            if 'program' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:program:{query_params.get("program")}'
                data = cache.get(cache_key)
                if data is None:
                    cache_student_data_task.delay(
                        request.user.id, student_groups=[(None, query_params.get("program"), None, None)],
                    )
                    return super().list(request, *args, **kwargs)
                logger.debug('Cache hit for %s', cache_key)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'program__department' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:department:{query_params.get("program__department")}'
                data = cache.get(cache_key)
                if data is None:
                    cache_student_data_task.delay(
                        request.user.id, student_groups=[(query_params.get("program__department"), None, None, None)],
                    )
                    return super().list(request, *args, **kwargs)
                logger.debug('Cache hit for %s', cache_key)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'student_class' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:class:{query_params.get("student_class")}'
                data = cache.get(cache_key)
                if data is None:
                    cache_student_data_task.delay(
                        request.user.id, student_groups=[(None, None, query_params.get("student_class"), None)],
                    )
                    return super().list(request, *args, **kwargs)
                logger.debug('Cache hit for %s', cache_key)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

            if 'status' in filter_params and len(filter_params)==1:
                cache_key = f'admin:students:status:{query_params.get("status")}'
                data = cache.get(cache_key)
                if data is None:
                    cache_student_data_task.delay(
                        request.user.id, student_groups=[(None, None, None, query_params.get("status"))],
                    )
                    return super().list(request, *args, **kwargs)
                logger.debug('Cache hit for %s', cache_key)
                page = self.paginate_queryset(data)
                if page is not None:
                    return self.get_paginated_response(page)
                return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)


    def perform_create(self, serializer):
        instance = serializer.save()
        cache_student_data_task.delay(
            self.request.user.id,
            student_groups=[(
                instance.program.department_id, instance.program_id,
                instance.student_class_id, instance.status,
            )],
        )





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
        previous = serializer.instance
        old_group = (
            previous.program.department_id, previous.program_id,
            previous.student_class_id, previous.status,
        )
        instance = serializer.save()
        new_group = (
            instance.program.department_id, instance.program_id,
            instance.student_class_id, instance.status,
        )
        cache_student_data_task.delay(
            self.request.user.id,
            student_groups=[old_group, new_group],
        )

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
    filterset_fields = ['department', 'total_semesters']
    search_fields = ['program_id', 'program_name']

    def list(self, request, *args, **kwargs):
        cache_key = f'admin:programs_list'
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

            if len(filter_params) == 1 and 'department' in filter_params:
                cache_key = f'admin:programs:department:{query_params.get("department")}'
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
    # `lab` is a relation now, but it stays a yes/no filter: ?lab=true asks for
    # courses that have a lab, not for one particular lab course.
    lab = django_filters.BooleanFilter(field_name='lab', lookup_expr='isnull', exclude=True)

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
            logger.debug('Cache miss for %s', cache_key)
            cache_courses_data_task.delay(request.user.id)
            return super().list(request, *args, **kwargs)

        else:
            filter_params = {
                key : value for key, value in request.query_params.items() if key!= 'page'
            }
            if filter_params:
                logger.debug('Cache miss for %s (filtered)', cache_key)
                return super().list(request, *args, **kwargs)

            page = self.paginate_queryset(cached_data)
            logger.debug('Cache hit for %s', cache_key)
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
        # A lab has no life apart from its theory course, so it goes too --
        # guarded by the same RESTRICT that protects any allocated course.
        # Either deletion failing rolls back both. Without the catch that
        # RESTRICT surfaces as a 500 rather than a readable 400.
        lab = instance.lab
        try:
            with transaction.atomic():
                instance.delete()
                if lab is not None:
                    lab.delete()
        except RestrictedError:
            raise ValidationError(
                {'detail': f"Course '{instance.course_code}' is allocated and cannot be deleted"}
            )
        cache_courses_data_task.delay(self.request.user.id)



class SessionListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = AcademicSession.objects.all()
    serializer_class = SessionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['period', 'year', 'status']
    search_fields = ['period', 'status']
    ordering_fields = ['year', 'period', 'status', 'activation_deadline', 'closing_deadline']

    def perform_create(self, serializer):
        serializer.save()


class SessionRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateAPIView
):
    queryset = AcademicSession.objects.all()
    serializer_class = SessionSerializer
    lookup_field = 'id'

    def perform_update(self, serializer):
        serializer.save()


class CurrentSessionView(APIView):
    """Unauthenticated — the login page and every role's UI need to know the
    live session phase (Initiated/Available/Active) before a JWT exists.

    At most one session is live at a time (guarded in
    SessionSerializer.update), so this returns a single object — or null when
    nothing is live, which is a normal state rather than an error."""
    serializer_class = CurrentSessionSerializer
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        session = AcademicSession.objects.filter(
            status__in=['Initiated', 'Available', 'Active']
        ).first()
        data = self.serializer_class(session).data if session else None
        return Response(data, status=status.HTTP_200_OK)


class SemesterListAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListAPIView
):
    serializer_class = SemesterSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['associated_class']

    def get_queryset(self):
        # Shared with cache_semester_data_task so the view and the cache can
        # never drift apart. Bare, a page of 10 cost 84 queries: 50 x Course
        # (one per semesterDetails row, for get_course_name), 10 x
        # semesterDetails, 10 x Class, and 10 x Program because Class.__str__
        # reads self.program.program_id.
        return _semester_cache_queryset()

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key : value for key, value in query_params.items() if key!='page' and value != ''
        }

        cache_key = 'admin:semesters_list'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            # Rebuild the unfiltered key plus the class actually asked for.
            # An empty list rebuilds only the unfiltered key, which the task
            # writes unconditionally.
            requested = filter_params.get('associated_class')
            cache_semester_data_task.delay(
                self.request.user.id,
                class_ids=[requested] if requested else [],
            )
            return  super().list(request, *args, **kwargs)
        
        if not query_params or not filter_params:
            logger.debug('Cache hit for %s', cache_key)
            page = self.paginate_queryset(cached_data)
            if page is not None:
                return self.get_paginated_response(page)
            return Response(cached_data, status=status.HTTP_200_OK)
        
        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if 'associated_class' in filter_params:
            cache_key= f'admin:semesters:class:{filter_params.get('associated_class')}'
            data = cache.get(cache_key)
            if data is None:
                cache_semester_data_task.delay(
                    self.request.user.id,
                    class_ids=[filter_params.get('associated_class')],
                )
                return super().list(request, *args, **kwargs)
            logger.debug('Cache hit for %s', cache_key)
            page = self.paginate_queryset(data)
            if page is not None:
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
        previous = serializer.instance
        old_class_id = previous.associated_class_id
        instance = serializer.save()
        cache_semester_data_task.delay(
            self.request.user.id,
            class_ids=[old_class_id, instance.associated_class_id],
        )


def _class_scheme_queryset():
    """Classes with everything ClassSerializer's scheme_of_studies needs.

    That field renders each class's semesters, their semesterDetails rows and
    each row's course. Without this, it re-queried all three per class -- 3
    queries a row, 30 of the 34 on a page of ten.
    """
    return Class.objects.prefetch_related(
        Prefetch('semester_set', queryset=Semester.objects.prefetch_related(
            Prefetch('semesterdetails_set',
                     queryset=SemesterDetails.objects.select_related('course')),
        )),
    )


class ClassListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    serializer_class = ClassSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['program', 'program__department','batch_year']
    search_fields = ['program', 'batch_year']

    def get_queryset(self):
        return _class_scheme_queryset()

    def perform_create(self, serializer):
        instance = serializer.save()
        cache_semester_data_task.delay(
            self.request.user.id,
            class_ids=[instance.class_id],
        )


    


class ClassRetrieveUpdateAPIView(
    IsSuperUserOrAdminMixin,
    generics.RetrieveUpdateAPIView
):
    serializer_class = ClassSerializer
    lookup_field = 'class_id'
    filter_backends = [OrderingFilter]
    ordering_fields = ['semesterdetails__semester__semester_no']

    def get_queryset(self):
        return _class_scheme_queryset()


class CourseAllocationListCreateAPIView (
    AdminCourseAllocationPermissionMixin,
    generics.ListCreateAPIView
):
    queryset = CourseAllocation.objects.all()
    serializer_class = CourseAllocationSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['faculty', 'status', 'course', 'semester']
    # `enrollment__student__student_id__first_name` was dropped. Searching a
    # reverse relation makes SearchFilter emit a correlated EXISTS subquery
    # over the enrollment table, which cost 324 ms and 321 ms of this
    # endpoint's 684 ms — for a search nobody performs, since you look a
    # student up on the student list, not on the allocation list.
    search_fields = ['faculty__employee_id__person_id', 'faculty__employee_id__first_name',
                     'faculty__employee_id__last_name',
                     'course__course_code', ]

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key : value for key,value in query_params.items() if key!='page' and value!=''
        }
        if not query_params or not filter_params:
            return super().list(request, *args, **kwargs)

        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if len(filter_params)==1 and ('semester' in filter_params or 'faculty' in filter_params):
            cache_key = f'admin:allocations:semester:{filter_params.get("semester")}' if 'semester' in filter_params else f'admin:allocations:faculty:{filter_params.get("faculty")}'
            data = cache.get(cache_key)
            if data is None:
                # Only the single missing key is stale -- no need to rebuild
                # every semester's and every faculty's allocation list to
                # answer a filter on one of them.
                if 'semester' in filter_params:
                    cache_courseAllocation_data_task.delay(
                        self.request.user.id, semester_ids=[filter_params['semester']],
                    )
                else:
                    cache_courseAllocation_data_task.delay(
                        self.request.user.id, faculty_ids=[filter_params['faculty']],
                    )
                return super().list(request, *args, **kwargs)
            page = self.paginate_queryset(data)
            if page is not None:
                return self.get_paginated_response(page)
            return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        instance = serializer.save()
        cache_courseAllocation_data_task.delay(
            self.request.user.id,
            semester_ids=[instance.semester_id],
            faculty_ids=[instance.faculty_id],
        )


class BulkCourseAllocationAPIView(
    IsSuperUserOrAdminMixin,
    APIView
):
    """The allocation worksheet for the live session.

    GET  — every class with a semester bound to the session, its scheme of
           studies, and who each course is currently allocated to. A null
           `allocation_id` marks a course still needing a teacher, which is
           what makes the screen safe to come back to.
    POST — batch create/update. See BulkCourseAllocationListSerializer for the
           per-phase rules.
    """
    serializer_class = BulkCourseAllocationSerializer

    LIVE_STATUSES = ['Initiated', 'Available']

    def _resolve_session(self, request):
        session_id = request.query_params.get('session')
        if session_id:
            return AcademicSession.objects.filter(id=session_id).first()
        # Only one session can be live at a time, so this is unambiguous.
        return AcademicSession.objects.filter(status__in=self.LIVE_STATUSES).first()

    def get(self, request, *args, **kwargs):
        session = self._resolve_session(request)
        if not session:
            return Response(
                {'session': None, 'classes': []}, status=status.HTTP_200_OK
            )

        # The worksheet is two kinds of data with very different lifetimes. The
        # class/semester/course skeleton comes from the scheme of studies and
        # barely moves once a session is initiated, but it carries the
        # expensive joins. Who each course is allocated to changes on every
        # POST. Caching them together would throw away the expensive half on
        # every write, so only the skeleton is cached and allocations are read
        # live.
        cache_key = f'admin:{session.id}:allocations:bulk'
        skeleton = cache.get(cache_key)
        if skeleton is None:
            skeleton = self._build_skeleton(session)
            cache.set(cache_key, skeleton, timeout=60 * 10)

        allocations = {
            (a.semester_id, a.course_id): a
            for a in CourseAllocation.objects
            .filter(semester__session=session)
            .select_related('faculty__employee_id')
        }

        classes = []
        for entry in skeleton:
            courses = []
            for course in entry['courses']:
                allocation = allocations.get((entry['semester_id'], course['course_code']))
                courses.append({
                    **course,
                    'allocation_id': allocation.allocation_id if allocation else None,
                    'faculty': {
                        'employee_id': allocation.faculty.employee_id.person_id,
                        'name': f'{allocation.faculty.employee_id.first_name} '
                                f'{allocation.faculty.employee_id.last_name}',
                    } if allocation else None,
                })
            classes.append({**entry, 'courses': courses})

        return Response({
            'session': {
                'id': session.id,
                'period': session.period,
                'year': session.year,
                'status': session.status,
            },
            'classes': classes,
        }, status=status.HTTP_200_OK)

    def _build_skeleton(self, session):
        """Classes, their semester for this session, and the courses each is
        scheduled to run. No allocation data — that is read live."""
        semesters = (
            Semester.objects
            .filter(session=session, status='Inactive')
            .select_related('associated_class__program')
            .prefetch_related('semesterdetails_set__course')
            .order_by('associated_class__program', 'associated_class__batch_year')
        )

        skeleton = []
        for semester in semesters:
            courses = [
                {
                    'course_code': detail.course.course_code,
                    'course_name': detail.course.course_name,
                    'credit_hours': detail.course.credit_hours,
                    'lab': detail.course.lab_id is not None,
                }
                for detail in semester.semesterdetails_set.all()
                if detail.course is not None   # placeholder row from class creation
            ]
            skeleton.append({
                'class_id': semester.associated_class_id,
                'class': str(semester.associated_class),
                'semester_id': semester.semester_id,
                'semester_no': semester.semester_no,
                'courses': courses,
            })
        return skeleton

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        # Every row names its own semester and faculty, so the batch's own
        # validated rows are exactly the set of keys it touched -- no need to
        # rebuild every other semester's and faculty's allocation list too.
        semester_ids = {row['semester'].pk for row in serializer.validated_data}
        faculty_ids = {row['faculty'].pk for row in serializer.validated_data}
        result = serializer.save()
        cache_courseAllocation_data_task.delay(
            request.user.id,
            semester_ids=list(semester_ids),
            faculty_ids=list(faculty_ids),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class CourseAllocationRetrieveUpdateDestroyAPIView(
    AdminCourseAllocationPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    # EnrollmentSerializer reads obj.student.student_id (Student, then Person)
    # and obj.result for every enrolled student, so serialising one allocation
    # cost 125 queries each for Student, person and result.
    queryset = CourseAllocation.objects.prefetch_related(
        Prefetch(
            'enrollment_set',
            queryset=Enrollment.objects.select_related('student__student_id', 'result'),
        )
    )
    serializer_class = CourseAllocationSerializer
    lookup_field = 'allocation_id'

    def perform_update(self, serializer):
        # semester is read-only on update (AdminModule/serializers.py), so
        # only faculty can move -- and only when it does, since that's the one
        # key an unchanged reassignment wouldn't need touching at all.
        previous = serializer.instance
        old_faculty_id = previous.faculty_id
        instance = serializer.save()
        cache_courseAllocation_data_task.delay(
            self.request.user.id,
            semester_ids=[instance.semester_id],
            faculty_ids=list({old_faculty_id, instance.faculty_id}),
        )

    def perform_destroy(self, instance):
        semester = instance.semester
        faculty_id = instance.faculty_id
        instance.delete()
        cache_courseAllocation_data_task.delay(
            self.request.user.id,
            semester_ids=[semester.semester_id],
            faculty_ids=[faculty_id],
        )
        from .tasks import cache_semester_enrollment_data_task
        cache_semester_enrollment_data_task.delay(semester.semester_id)




class EnrollmentListCreateAPIView(
    AdminEnrollmentPermissionMixin,
    generics.ListCreateAPIView
):

    serializer_class = EnrollmentSerializer
    queryset = Enrollment.objects.select_related('student__student_id', 'result')

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['student','allocation__faculty',
                        'status', 'allocation__semester','result__course_gpa']

    search_fields = ['student__student_id__person_id', 'student__student_id__first_name',
                     'student__student_id__last_name']

    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        filter_params = {
            key: value for key, value in query_params.items() if key != 'page' and value != ''
        }
        if not query_params or not filter_params:
            return super().list(request, *args, **kwargs)

        if 'ordering' in filter_params or 'search' in filter_params:
            return super().list(request, *args, **kwargs)

        if len(filter_params) == 1 and ('student' in filter_params or 'allocation__faculty' in filter_params):
            cache_key = f'admin:enrollments:student:{filter_params.get("student")}' if 'student' in filter_params else f'admin:enrollments:faculty:{filter_params.get("allocation__faculty")}'
            data = cache.get(cache_key)
            if data is None:
                # One key is missing, so rebuild that one key. This used to
                # rebuild all ~5,200, which never finished before the next
                # request arrived.
                if 'student' in filter_params:
                    cache_enrollment_data_task.delay(
                        self.request.user.id,
                        student_ids=[filter_params['student']],
                    )
                else:
                    cache_enrollment_data_task.delay(
                        self.request.user.id,
                        faculty_ids=[filter_params['allocation__faculty']],
                    )
                return super().list(request, *args, **kwargs)
            page = self.paginate_queryset(data)
            if page is not None:
                return self.get_paginated_response(page)
            return Response(data, status=status.HTTP_200_OK)

        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        instance = serializer.save()
        cache_enrollment_data_task.delay(
            self.request.user.id,
            student_ids=[instance.student_id],
            faculty_ids=[instance.allocation.faculty_id],
        )



class EnrollmentRetrieveUpdateDestroyAPIView(
    AdminEnrollmentPermissionMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = Enrollment.objects.all()
    serializer_class = EnrollmentSerializer
    lookup_field = 'enrollment_id'

    def perform_update(self, serializer):
        # Read the old owners before saving: an update can move the enrollment
        # to a different student or a different allocation, which leaves the
        # previous student's and previous teacher's keys wrong too.
        previous = serializer.instance
        student_ids = [previous.student_id]
        faculty_ids = [previous.allocation.faculty_id]

        instance = serializer.save()

        student_ids.append(instance.student_id)
        faculty_ids.append(instance.allocation.faculty_id)
        cache_enrollment_data_task.delay(
            self.request.user.id,
            student_ids=student_ids,
            faculty_ids=faculty_ids,
        )

    def perform_destroy(self, instance):
        result = Result.objects.get(enrollment=instance.enrollment_id)
        if result.course_gpa:
            raise PermissionDenied('This enrollment cannot be deleted')
        else:
            # Read them off before the delete; afterwards there is nothing to
            # read them from.
            student_id = instance.student_id
            faculty_id = instance.allocation.faculty_id
            instance.delete()
            cache_enrollment_data_task.delay(
                self.request.user.id,
                student_ids=[student_id],
                faculty_ids=[faculty_id],
            )



class TranscriptListCreateAPIView(
    IsSuperUserOrAdminMixin,
    generics.ListCreateAPIView
):
    queryset = Transcript.objects.all()
    serializer_class = TranscriptSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['semester', 'student']
    search_fields = ['student__student_id__person_id', 'student__student_id__first_name',
                     'student__student_id__last_name']

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
                transcripts = serializer.save()
                # bulk_create returns a plain list, which has no `.data` —
                # serialize it before responding.
                return Response(
                    TranscriptSerializer(transcripts, many=True).data,
                    status=status.HTTP_201_CREATED,
                )
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
        200: OpenApiResponse(
            description="Request Confirmation Success",
            examples=[
                OpenApiExample(
                    'Success Example',
                    value={'message': 'Change Request confirmation successfully'},
                )
            ]
        ),
        400: OpenApiResponse(
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
        change_request.confirmed_at = timezone.now()
        change_request.save()
        if change_request.change_type == 'result_calculation':
            send_result_calculation_confirmation_mail.apply_async(args=[change_request.pk],eta=timezone.now()+timedelta(minutes=2))

        Notification.objects.create(
            recipient=change_request.requested_by,
            verb='change_request_confirmed',
            message=f'Your {change_request.get_change_type_display()} request has been confirmed.',
            level='info',
            content_type=ContentType.objects.get_for_model(ChangeRequest),
            object_id=change_request.pk,
        )

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
            return Response(result, status=status.HTTP_201_CREATED)


    def get(self, request, *args, **kwargs):
        if not request.query_params.get('type'):
            return Response({"error": "Template type not specified. Options : ['student', 'faculty]"}, status=status.HTTP_400_BAD_REQUEST)

        target_model = request.query_params.get('type')

        file_headers = ['password','image','first_name','last_name','father_name','gender','cnic','dob','contact_number','institutional_email','personal_email',
                          'religion','country','province','city','zipcode','street_address','degree_title_1','education_board_1','institution_1','passing_year_1',
                          'total_marks_1','obtained_marks_1','is_current_1','degree_title_2','education_board_2','institution_2','passing_year_2','total_marks_2','obtained_marks_2'
                         ,'is_current_2','degree_title_3','education_board_3','institution_3','passing_year_3',
                          'total_marks_3','obtained_marks_3','is_current_3','degree_title_4','education_board_4','institution_4','passing_year_4',
                          'total_marks_4','obtained_marks_4','is_current_4','degree_title_5','education_board_5','institution_5','passing_year_5',
                          'total_marks_5','obtained_marks_5','is_current_5']

        if target_model == 'faculty':
            file_headers.append('department',)
            file_headers.append('designation',)
            file_headers.append('joining_date',)

        if target_model == 'student':
            file_headers.append('program',)
            file_headers.append('student_class',)
            file_headers.append('admission_date',)


        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(file_headers)

        template = buffer.getvalue()
        buffer.close()

        return HttpResponse(template, content_type='text/csv',
                            headers={'Content-Disposition': f'attachment; filename={target_model}_template.csv'})








