from rest_framework.permissions import IsAuthenticated

from .permissions import *


class StudentPermissionMixin:
    permission_classes = [IsAuthenticated,StudentPermissions]


class ReviewsPermissionMixin:
    permission_classes = [IsAuthenticated, ReviewPermission]

class StudentEnrollmentPermissionMixin:
    permission_classes = [IsAuthenticated, StudentEnrollmentPermission]

class StudentAssessmentUploadPermissionMixin:
    permission_classes = [IsAuthenticated, StudentAssessmentUploadPermission]

class StudentEnrollmentCreatePermissionMixin:
    permission_classes = [IsAuthenticated, StudentEnrollmentCreatePermission]