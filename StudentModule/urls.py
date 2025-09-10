from django.urls import path
from .views import *

app_name = 'Student'

urlpatterns = [
    path ('dashboard/', StudentDashboardView.as_view(), name='student-dashboard'),
    path ('profile/', StudentProfileView.as_view(), name='student-profile'),
    path ('enrollments/', StudentEnrollmentsListView.as_view(), name='student-enrollments'),
    path ('enrollments/<int:enrollment_id>/', StudentEnrollmentRetrieveView.as_view(), name='enrollment-detail'),
    path ('enrollments/<int:enrollment_id>/assessments/<int:assessment_id>/file-upload/<int:id>/', StudentAssessmentUploadView.as_view(), name='assessment-upload'),

    path ('attendance/', StudentAttendanceListAPIView.as_view(), name='student-attendance'),
    path ('attendance/<int:enrollment_id>/', StudentAttendanceRetrieveAPIView.as_view(), name='attendance-detail'),

    path('<str:student_id>/enrollments/reviews/', ReviewListAPIView.as_view()),
    path('<str:student_id>/enrollments/<int:enrollment_id>/reviews/', ReviewCreateAPIView.as_view()),
    path('<str:student_id>/enrollments/<int:enrollment_id>/reviews/<int:review_id>/', ReviewRetrieveUpdateDestroyAPIView.as_view(), name='review-detail'),

    path ('enrollments/create/', StudentEnrollmentCreateAPIView.as_view()),
    path ('compilers/', StudentCompilerAPIView.as_view()),
]