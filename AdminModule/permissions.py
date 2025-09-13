from rest_framework import permissions
from Models.models import CourseAllocation, Semester


class IsSuperUserOrAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser or request.user.groups.filter(name='Admin').exists():
                return True
            return False
        return False

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class AdminPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if  request.user.groups.filter(name='Admin').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.groups.filter(name='Admin').exists():
                return request.user == obj.employee_id.user
            return False
        return False



class ChangeRequestPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser or request.user.groups.filter(name='Admin').exists():
                return True
            return False
        return False
    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists() and obj.requested_by == request.user:
                if obj.status == 'Applied':
                    return request.method == 'GET'
                else:
                    return request.method == 'PATCH' or request.method == 'PUT'
            return False
        return False

class DepartmentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
        return False
    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


class AdminCourseAllocationPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                queryset = Semester.objects.filter(status='Inactive',session__isnull=False, activation_deadline__isnull=False)
                if queryset.exists():
                    return True
                else:
                    return request.method == 'GET'

            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                if obj.status in ['Ongoing', 'Completed',]:
                    return request.method == 'GET'
                elif obj.status == 'Inactive':
                    return True
            return False


class AdminEnrollmentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                queryset = CourseAllocation.objects.filter(status='Ongoing')
                if queryset:
                    return True
                else:
                    return request.method == 'GET'
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                if obj.status in ['Active', 'Inactive', 'Dropped']:
                    return True
                elif obj.status == 'Completed':
                    return request.method == 'GET'
                return False
            return False