from rest_framework import permissions
from Models.models import CourseAllocation, Semester


class IsSuperUserOrAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser or request.user.groups.filter(name='Admin').exists():
                return True
            return False
        return False


class AdminPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if  request.user.groups.filter(name='Admin').exists():
                return request.method == 'GET' or request.method == 'PUT' or request.method == 'PATCH'
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        if request.user.groups.filter(name='Admin').exists():
            return request.user == obj.employee_id.user
        return False



class ChangeRequestPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                    return True
            if request.user.groups.filter(name='Admin').exists():
                return request.method in ['GET', 'PUT', 'PATCH']
            return False
        return False

    def has_object_permission(self, request, view, obj):
        if obj.requested_by == request.user:
            if obj.status == 'Applied':
                return request.method == 'GET'
            else:
                return request.method == 'PATCH' or request.method == 'PUT'
        return False

class DepartmentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                return request.method in ['GET', 'PUT', 'PATCH']
        return False


class AdminCourseAllocationPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                if request.method in permissions.SAFE_METHODS:
                    return True

                # Setting allocations up is a bulk activity during Initiated.
                if Semester.objects.filter(
                    status='Inactive', session__status='Initiated'
                ).exists():
                    return request.method in ['POST', 'DELETE']

                # Once enrollment opens the worksheet is closed, but a single
                # faculty correction is still needed — and DELETE is no longer
                # possible anyway, since Enrollment.allocation is RESTRICT.
                # The serializer narrows this to the `faculty` field.
                if Semester.objects.filter(
                    status='Inactive', session__status='Available'
                ).exists():
                    return request.method in ['PATCH', 'PUT']

                return False

            return False
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        if request.user.groups.filter(name='Admin').exists():
            if obj.status in ['Active', 'Completed',]:
                return request.method == 'GET'
            # A locked allocation is not blocked here — the serializer keeps
            # every field read-only except passing_threshold, so results can be
            # recalculated under a different cutoff.
            elif obj.status in ['Inactive', 'Locked']:
                return True
        return False



class AdminEnrollmentPermissions(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                queryset = CourseAllocation.objects.filter(status='Active').exists()
                if queryset:
                    return True
                else:
                    return request.method == 'GET'
            return False
        return False

    def has_object_permission(self, request, view, obj):
            if request.user.is_superuser:
                return True
            if request.user.groups.filter(name='Admin').exists():
                if obj.status in ['Active', 'Inactive', 'Dropped']:
                    return True
                # Locked and Completed enrollments are read-only: marks are
                # frozen and results either exist or are being calculated.
                elif obj.status in ['Locked', 'Completed']:
                    return request.method == 'GET'
                return False
            return False