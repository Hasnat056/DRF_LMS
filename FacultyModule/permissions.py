from rest_framework import permissions


class FacultyPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return obj.employee_id.user == request.user
            return False
        return False

class FacultyCourseAllocationPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return obj.teacher_id.employee_id.user == request.user
            return False
        return False



class AssessmentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                return request.method in permissions.SAFE_METHODS
            if request.user.groups.filter(name='Faculty').exists():
                return True
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return request.user == obj.allocation_id.teacher_id.employee_id.user
            return True
        return False

class FacultyRequestsPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False
    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return obj.requested_by == request.user
            return False
        return False


class FacultyLecturePermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return True
            return False
        return False
    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Faculty').exists():
                return obj.allocation_id.teacher_id.employee_id.user == request.user
            return False
        return False