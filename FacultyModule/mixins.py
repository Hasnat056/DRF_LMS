from rest_framework.permissions import IsAuthenticated
from .permissions import *

class FacultyPermissionMixin:
    permission_classes = [IsAuthenticated, FacultyPermissions]

class FacultyCourseAllocationPermissionMixin:
    permission_classes = [IsAuthenticated, FacultyCourseAllocationPermissions]

class FacultyAssessmentPermissionMixin:
    permission_classes = [IsAuthenticated, AssessmentPermissions]

class FacultyRequestPermissionMixin:
    permission_classes = [IsAuthenticated, FacultyRequestsPermissions]

class FacultyLecturePermissionMixin:
    permission_classes = [IsAuthenticated, FacultyLecturePermissions]