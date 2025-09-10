from django.urls import path
from .views import *

app_name = 'Admin'
urlpatterns =[

    path ('profile/', AdminProfileAPIView.as_view(), name='admin-profile'),

    path ('dashboard/',AdminDashboardAPIView.as_view()),

    path('faculty/', FacultyListCreateAPIView.as_view()),
    path('faculty/<str:employee_id>/', FacultyRetrieveUpdateAPIView.as_view(), name='faculty-detail'),

    path('students/', StudentListCreateAPIView.as_view()),
    path('students/<str:student_id>/', StudentRetrieveUpdateAPIView.as_view(), name='student-detail'),

    path('change-request/confrim/<uuid:token>/', ChangeRequestView.as_view(), name='confirm-change-request'),

    path('departments/', DepartmentListAPIView.as_view()),
    path('departments/<int:department_id>/', DepartmentRetrieveUpdateAPIView.as_view(), name='department-detail'),

    path('programs/', ProgramListCreateAPIView.as_view()),
    path('programs/<str:program_id>/', ProgramRetrieveUpdateDestroyAPIView.as_view(), name='program-detail'),

    path('courses/', CourseListCreateAPIView.as_view()),
    path('courses/<str:course_code>/', CourseRetrieveUpdateDestroyAPIView.as_view(), name='course-detail'),

    path ('semesters/', SemesterListAPIView.as_view()),
    path ('semesters/<int:semester_id>/', SemesterRetrieveUpdateAPIView.as_view(), name='semester-detail'),
    path ('semesters/<int:semester_id>/transcripts-create/', TranscriptBulkCreateAPIView.as_view(), name='semester-transcripts-create'),

    path('classes/', ClassListCreateAPIView.as_view()),
    path('classes/<int:class_id>/', ClassRetrieveUpdateAPIView.as_view(), name='class-detail'),

    path('allocations/',CourseAllocationListCreateAPIView.as_view()),
    path('allocations/<int:allocation_id>/', CourseAllocationRetrieveUpdateDestroyAPIView.as_view(), name='allocation-detail'),

    path('enrollments/', EnrollmentListCreateAPIView.as_view()),
    path('enrollments/<enrollment_id>/', EnrollmentRetrieveUpdateDestroyAPIView.as_view(), name='enrollment-detail'),

    path('transcripts/', TranscriptListCreateAPIView.as_view()),

    path('change-requests/', ChangeRequestListAPIView.as_view()),
    path('change-requests/<int:pk>/', ChangeRequestRetrieveUpdateAPIView.as_view(), name='change_request-detail'),

    path('bulk/', BulkCreateAPIView.as_view()),

]