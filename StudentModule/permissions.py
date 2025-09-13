from rest_framework import permissions
from Models.models import Student, Semester

class StudentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False

    def has_object_permission(self, request, view, obj):
            return obj.student_id.user == request.user



class ReviewPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser or request.user.groups.filter(name='Student').exists():
                return not request.method == 'DELETE'
            if request.user.groups.filter(name='Admin').exists() or request.user.groups.filter(name='Faculty').exists():
                return request.method in permissions.SAFE_METHODS

    def has_object_permission(self, request, view, obj):
            if  request.user.groups.filter(name='Student').exists():
                return request.user == obj.enrollment_id.student_id.student_id.user

            if request.user.groups.filter(name='Faculty').exists():
                return request.user == obj.enrollment_id.allocation_id.teacher_id.employee_id.user
            return False


class StudentEnrollmentPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return request.method in permissions.SAFE_METHODS
            return False
        return False
    def has_object_permission(self, request, view, obj):
            return request.user == obj.student_id.student_id.user


class StudentAssessmentUploadPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return not request.method == 'POST'
            return False
        return False

    def has_object_permission(self, request, view, obj):
            return obj.enrollment_id.student_id.student_id.user == request.user


class StudentEnrollmentCreatePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                student = Student.objects.get(student_id__user=request.user)
                semester = Semester.objects.filter(semesterdetails__class_id=student.class_id, status='Inactive',
                                                   session__isnull=False,
                                                   activation_deadline__isnull=False).prefetch_related('courseallocation_set').first()
                if not semester or not semester.courseallocation_set.exists():
                    return False
                return True
            return False
        return False
