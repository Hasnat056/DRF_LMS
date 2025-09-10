from django.urls import path


from .views import *

app_name = 'Faculty'
urlpatterns = [

    path ('dashboard/', FacultyDashboardView.as_view() ,name='faculty-dashboard'),
    path ('profile/', FacultyProfileView.as_view() , name='faculty-profile'),
    path ('allocations/', FacultyCourseAllocationView.as_view() , name='faculty-course-allocation'),
    path ('allocations/<int:allocation_id>/', FacultyCourseAllocationRetrieveView.as_view() , name='allocation-detail'),
    path ('allocations/<int:allocation_id>/calculate-result/',ResultCalculationRequest.as_view() , name='allocation-calculate-result'),

    path ('allocations/<int:allocation_id>/assessments/' ,AssessmentListCreateAPIView.as_view()),
    path('allocations/<int:allocation_id>/assessments/<int:assessment_id>/', AssessmentRetrieveUpdateDestroyAPIView.as_view(), name='assessment-detail'),

    path('allocations/<int:allocation_id>/lectures/' ,LectureListCreateAPIView.as_view()),
    path('allocations/<int:allocation_id>/lectures/<str:lecture_id>/', LectureRetrieveUpdateDestroyAPIView.as_view(), name='lecture-detail'),

    path('requests/', FacultyRequestsListView.as_view(), name='change-request'),
    path('requests/<int:pk>/', FacultyRequestsUpdateView.as_view(), name='change-request-update'),

]