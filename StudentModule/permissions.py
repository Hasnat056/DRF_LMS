from django.core.cache import cache
from rest_framework import permissions
from Models.models import Student, Semester, CourseAllocation


class StudentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return request.method in ['GET', 'PUT', 'PATCH']
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
            return False
        return False

    def has_object_permission(self, request, view, obj):
            if  request.user.groups.filter(name='Student').exists():
                return request.user == obj.enrollment.student.student_id.user

            if request.user.groups.filter(name='Faculty').exists():
                return request.user == obj.enrollment.allocation.faculty.employee_id.user
            return False


class StudentEnrollmentPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return request.method in permissions.SAFE_METHODS
            return False
        return False
    def has_object_permission(self, request, view, obj):
            return request.user == obj.student.student_id.user


class StudentAssessmentUploadPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                return not request.method == 'POST'
            return False
        return False

    def has_object_permission(self, request, view, obj):
            return obj.enrollment.student.student_id.user == request.user


class StudentEnrollmentCreatePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Student').exists():
                student = Student.objects.filter(student_id__user=request.user).first()
                cache_key = f'enrollments:{student.student_class.class_id}:semester:allocations'
                if not student or not cache.get(cache_key):
                    return False
                request.student = student
                return True
            return False
        return False
