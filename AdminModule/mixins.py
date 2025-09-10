from django.contrib.auth.models import User, Group
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
import statistics
from Models.models import *
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import send_mail
from django.urls import reverse
from DjangoRESTProject_practice import settings

from .permissions import *

class PersonSerializerMixin:
    def create_mixin(self, validated_data, model):
        person_data = {}
        if model == 'Student':
            person_data = validated_data.pop('student_id')
        if model in ['Faculty', 'Admin']:
            person_data = validated_data.pop('employee_id')


        user_data = person_data.pop('user', {})
        address_data = person_data.pop('address', {})
        qualification_data = person_data.pop('qualification_set',[])
        model_data = validated_data
        instance = None
        user = None
        person= None
        if user_data:
            user = User.objects.create_user(**user_data, username=person_data['institutional_email'])

        if person_data and model_data:
            if model == 'Faculty':
                person = Person.objects.create(**person_data, type='Faculty', user=user)
                faculty = Faculty.objects.create(**model_data, employee_id=person)
                group = Group.objects.get(name="Faculty")
                user.groups.add(group)
                instance = faculty
            elif model == 'Student':
                person = Person.objects.create(**person_data, type='Student', user=user)
                student = Student.objects.create(**model_data, student_id=person)
                group = Group.objects.get(name="Student")
                user.groups.add(group)
                instance = student
            elif model == 'Admin':
                person = Person.objects.create(**person_data, type='Admin', user=user)
                admin = Admin.objects.create(**model_data, employee_id=person)
                group = Group.objects.get(name="Admin")
                user.groups.add(group)
                instance = admin

        if address_data:
            Address.objects.create(**address_data, person_id=person)

        if qualification_data:
            if qualification_data:
                for each in qualification_data:
                    Qualification.objects.create(person_id=person, **each)

        return instance


    def update_mixin(self, instance, validated_data):
        person_data = {}
        person = None
        if isinstance(instance, Faculty) or  isinstance(instance, Admin):
            person_data = validated_data.pop('employee_id')
            person = instance.employee_id
        if isinstance(instance, Student):
            person_data = validated_data.pop('student_id')
            person = instance.student_id

        if 'user' in person_data:
            user_data = person_data.pop('user')
            user = person.user
            if user_data:
                for attr, value in user_data.items():
                    setattr(user, attr, value)
                user.save()

        address_data = person_data.pop('address')
        qualification_data = person_data.pop('qualification_set',[])
        model_data = validated_data

        if model_data:
            for attr, value in model_data.items():
                setattr(instance, attr, value)
            instance.save()


        if person_data:
            for attr, value in person_data.items():
                if attr == 'image' and not value:
                    continue
                setattr(person, attr, value)
            person.save()



        address = person.address
        if address_data:
            for attr, value in address_data.items():
                setattr(address, attr, value)
            address.save()

        if qualification_data:
            if hasattr(person, 'qualification_set'):
                person.qualification_set.all().delete()
            for each in qualification_data:
                qualification = Qualification.objects.create(person_id=person, **each)
                print(qualification)

        return instance

    def destroy_mixin(self):
        instance = self.get_object()
        target_field = {self.target_field_name : instance}
        change_type = self.change_type
        person = None
        if isinstance(instance, Faculty):
            person = instance.employee_id
        if isinstance(instance, Student):
            person = instance.student_id

        if ChangeRequest.objects.filter(**target_field, status='pending').exists():
            return Response({"message": f"{person.person_id} has already a pending deletion request."})

        change_request = ChangeRequest.objects.create(
            change_type=change_type,
            status='pending',
            requested_by=self.request.user,
            **target_field
        )

        confirmation_link = self.request.build_absolute_uri(
            reverse('confirm-change-request', args=[change_request.confirmation_token])
        )

        send_mail(
            subject=f"Delete Request : {person.person_id}",
            message=f"Dear {person.first_name} {person.last_name},\n"
                    f"A request has been made to delete the record of {person.person_id} from the system. This action will permanently remove all related data and cannot be undone.\n"
                    f"If you requested this change, please confirm by clicking the link below:\n"
                    f"Confirmation link : {confirmation_link} \n"
                    f"The links will expire in 48 hours.\n"

                    f"Thank you,\n"
                    f"NAMAL UNIVERSITY, MAINWALI",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[person.institutional_email],
        )
        return Response({"message": f"Deletion email has been sent successfully to {person.person_id}"},status=status.HTTP_200_OK)


class ResultCalculationMixin:
    def calculate_gpa(self, data):
        results = Result.objects.filter(enrollment_id__in=data.keys())
        values = list(data.values)
        final_result_data = {}
        if len(values) < 20:
            for enrollment, obtained in data.items():
                if obtained >= 85:
                    course_gpa = 4.0
                elif obtained >= 80:
                    course_gpa = 3.67
                elif obtained >= 75:
                    course_gpa = 3.33
                elif obtained >= 70:
                    course_gpa = 3.0
                elif obtained >= 65:
                    course_gpa = 2.67
                elif obtained >= 61:
                    course_gpa = 2.33
                elif obtained >= 58:
                    course_gpa = 2.0
                elif obtained >= 55:
                    course_gpa = 1.67
                elif obtained >= 50:
                    course_gpa = 1.0
                else:
                    course_gpa = 0.0

                final_result_data[enrollment.student_id] = {'obtained': obtained, 'course_gpa': course_gpa}
                student_result = results.get(enrollment_id=enrollment)
                student_result.obtained_marks = obtained
                student_result.course_gpa = course_gpa
                student_result.save()

            return final_result_data

        mean = statistics.mean(values)
        standard_deviation = statistics.pstdev(values)
        final_result_data = {'mean': mean, 'standard_deviation': standard_deviation}

        for enrollment, obtained in data.items():
            course_gpa = 0.00
            score = (obtained - mean) / standard_deviation
            if score >= 1.5:
                course_gpa = 4.0
            elif score >= 1.0:
                course_gpa = 3.67
            elif score >= 0.5:
                course_gpa = 3.33
            elif score >= 0.0:
                course_gpa = 3.0
            elif score >= -0.5:
                course_gpa = 2.67
            elif score >= -1.0:
                course_gpa = 2.33
            elif score >= -1.5:
                course_gpa = 2.0
            elif score >= -2.0:
                course_gpa = 1.67
            elif score >= -2.5:
                course_gpa = 1.33
            elif score >= -3.0:
                course_gpa = 1.0
            else:
                course_gpa = 0.0

            final_result_data[enrollment.student_id] = {'obtained': obtained, 'score': score, 'course_gpa': course_gpa}
            student_result = results.get(enrollment_id=enrollment)
            student_result.obtained_marks = obtained
            student_result.course_gpa = course_gpa
            student_result.save()

        return final_result_data

    def calculate_result(self, instance):
        results = {}
        if isinstance(instance, CourseAllocation):
            enrollments = Enrollment.objects.filter(allocation_id=instance).prefetch_related('assessmentchecked_set')
            for each_enrollemnt in enrollments:
                if 'assessmentchecked_set' in each_enrollemnt:
                    student_result = 0.00
                    for each_assessment in each_enrollemnt.assessmentchecked_set.all():
                        student_result += ((
                                                       each_assessment.obtained / each_assessment.assessment_id.total_marks) * each_assessment.assessment_id.weightage)

                    results[each_enrollemnt] = student_result
                    each_enrollemnt.status = 'Completed'
                    each_enrollemnt.save()
            return self.calculate_gpa(results)
        else:
            return {'message': 'Valid course allocation instance not provided.'}



class AdminPermissionMixin:
    permission_classes = [IsAuthenticated, AdminPermissions]

class ChangeRequestPermissionMixin:
    permission_classes = [IsAuthenticated, ChangeRequestPermissions]

class DepartmentPermissionMixin:
    permission_classes = [IsAuthenticated, DepartmentPermissions]

class AdminCourseAllocationPermissionMixin:
    permission_classes = [IsAuthenticated, AdminCourseAllocationPermissions]

class AdminEnrollmentPermissionMixin:
    permission_classes = [IsAuthenticated, AdminEnrollmentPermissions]

class IsSuperUserOrAdminMixin:
    permission_classes = [IsAuthenticated,IsSuperUserOrAdminPermission]

