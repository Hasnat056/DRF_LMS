import threading
from django.db.models.signals import post_save, post_delete, pre_save
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone
from rest_framework.generics import get_object_or_404
from .models import *


_thread_locals = threading.local()

def set_current_request(request):
    _thread_locals.request = request

def get_current_request():
    return getattr(_thread_locals, 'request', None)



def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    return ip


def get_current_user_person(request):
    if not request or not request.user.is_authenticated:
        return None
    try:
        return get_object_or_404(Person, employee_id__user=request.user)
    except Person.DoesNotExist:
        return None

def serialize_instance(instance):

    data = {}
    for field in instance._meta.fields:
        data[field.name] = getattr(instance, field.name)
    return data

def get_changed_fields(old_values, new_values):

    changed = {}
    for key, new_val in new_values.items():
        old_val = old_values.get(key)
        if old_val != new_val:
            changed[key] = {"old": old_val, "new": new_val}
    return changed

def log_audit_trail(request, entity_name, action_type, old_values, new_values):
    user_person = get_current_user_person(request)
    if not user_person:
        return

    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '') if request else ''

    AuditTrail.objects.create(
        userid=user_person,
        action_type=action_type,
        entity_name=entity_name,
        time_stamp=timezone.now(),
        ip_address=ip_address,
        user_agent=user_agent,
        old_value=old_values,
        new_value=new_values
    )


@receiver(pre_save)
def capture_old_values(sender, instance, **kwargs):
    tracked_models = (
        Person, Faculty, Student, Enrollment, CourseAllocation, Course,
        Program, Class, Semester, SemesterDetails, Lecture, Assessment,
        AssessmentChecked, Attendance, Qualification, Address
    )

    if sender not in tracked_models:
        return

    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            instance._old_values = serialize_instance(old_instance)
        except sender.DoesNotExist:
            instance._old_values = {}
    else:
        instance._old_values = {}



@receiver(post_save)
def log_create_update(sender, instance, created, **kwargs):
    tracked_models = (
        Person, Faculty, Student, Enrollment, CourseAllocation, Course,
        Program, Class, Semester, SemesterDetails, Lecture, Assessment,
        AssessmentChecked, Attendance, Qualification, Address
    )
    if sender not in tracked_models:
        return

    request = get_current_request()
    new_values = serialize_instance(instance)

    if created:
        log_audit_trail(
            request=request,
            entity_name=sender.__name__,
            action_type="CREATE",
            old_values={},
            new_values=new_values
        )
    else:
        old_values = getattr(instance, "_old_values", {})
        changed_fields = get_changed_fields(old_values, new_values)
        if changed_fields:
            log_audit_trail(
                request=request,
                entity_name=sender.__name__,
                action_type="UPDATE",
                old_values={k: v["old"] for k, v in changed_fields.items()},
                new_values={k: v["new"] for k, v in changed_fields.items()}
            )



@receiver(post_delete)
def log_delete(sender, instance, **kwargs):
    tracked_models = (
        Person, Faculty, Student, Enrollment, CourseAllocation, Course,
        Program, Class, Semester, SemesterDetails, Lecture, Assessment,
        AssessmentChecked, Attendance, Qualification, Address
    )
    if sender not in tracked_models:
        return

    request = get_current_request()
    old_values = serialize_instance(instance)

    log_audit_trail(
        request=request,
        entity_name=sender.__name__,
        action_type="DELETE",
        old_values=old_values,
        new_values={}
    )





