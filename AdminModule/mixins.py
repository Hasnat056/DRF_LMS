import logging
from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.db.models import Prefetch
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
import statistics
from Models.models import *
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import send_mail
from django.urls import reverse
from NexusAPI import settings

from .permissions import *

logger = logging.getLogger(__name__)

class PersonSerializerMixin:
    def create_mixin(self, validated_data, model):
        person_data = {}
        if model == 'Student':
            person_data = validated_data.pop('student_id', {})
        if model in ['Faculty', 'Admin']:
            person_data = validated_data.pop('employee_id', {})


        user_data = person_data.pop('user', {})
        address_data = person_data.pop('address', {})
        qualification_data = person_data.pop('qualification_set',[])
        model_data = validated_data
        instance = None
        user = None
        person= None
        if user_data:
            user = User.objects.create_user(**user_data, username=person_data['institutional_email'])
            user.save()

        if person_data and model_data:
            if model == 'Faculty':
                count = Faculty.objects.filter(department=model_data['department']).count()
                person_id = f'NUM-{model_data["department"]}-{str(timezone.now().year)}-{str(count+1)}'
                person_data['person_id'] = person_id
                person = Person.objects.create(**person_data, type='Faculty', user=user)
                faculty = Faculty.objects.create(**model_data, employee_id=person)
                group = Group.objects.get(name="Faculty")
                user.groups.add(group)
                instance = faculty
            elif model == 'Student':
                count = Student.objects.filter(program=model_data['program'], admission_date__year=timezone.now().year).count()
                person_id = f'NUM-{model_data['program']}-{str(timezone.now().year)}-{str(count+1)}'
                person_data['person_id'] = person_id
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
                    Qualification.objects.create(person=person, **each)

        return instance


    def update_mixin(self, instance, validated_data):
        person_data = {}
        person = None
        if isinstance(instance, Faculty) or  isinstance(instance, Admin):
            person_data = validated_data.pop('employee_id', {})
            person = instance.employee_id
        if isinstance(instance, Student):
            person_data = validated_data.pop('student_id', {})
            person = instance.student_id

        if person_data and ('user' in person_data):
            user_data = person_data.pop('user')
            user = person.user
            if user_data:
                for attr, value in user_data.items():
                    setattr(user, attr, value)
                user.save()

        address_data = person_data.pop('address', {}) #fixed bug (testing)
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


        if address_data:
            address = person.address  if hasattr(person, 'address') else Address.objects.create(person_id=person) #fixed bug (testing)
            for attr, value in address_data.items():
                setattr(address, attr, value)
            address.save()

        if qualification_data:
            if hasattr(person, 'qualification_set'):
                person.qualification_set.all().delete()
            for each in qualification_data:
                qualification = Qualification.objects.create(person=person, **each)
                logger.debug('Created qualification %s for person %s', qualification, person.person_id)

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

        from . import email_service
        email_service.send_delete_request(person, confirmation_link)
        return Response({"message": f"Deletion email has been sent successfully to {person.person_id}"},status=status.HTTP_200_OK)


class ResultCalculationMixin:
    """Grading per the university handbook.

    Class of 20 or more -> relative (RGS); fewer -> absolute (AGS).

    In both systems an instructor may fix a passing threshold for the course.
    Students below it are failed, and — this is the part that makes the
    threshold move everyone's grade — they are excluded from the cohort before
    the mean and standard deviation are computed:

        "Below this threshold, students are declared as Failed and are not
         included for assigning grades as described above."

    So raising the threshold drops the weakest marks out of the statistics,
    lifting the mean, which shifts every remaining student's z-score.
    """

    #: Default pass mark; an allocation may set its own between 30 and 50.
    DEFAULT_PASSING_THRESHOLD = 50

    #: Below this many students the absolute system is used instead.
    RELATIVE_GRADING_MIN_CLASS_SIZE = 20

    #: Table 3 — absolute grading. (minimum mark, grade point).
    ABSOLUTE_BANDS = [
        (85, 4.00),   # A+ (>=95) and A share 4.00
        (80, 3.67),   # A-
        (75, 3.33),   # B+
        (71, 3.00),   # B
        (68, 2.67),   # B-
        (64, 2.33),   # C+
        (61, 2.00),   # C
        (58, 1.67),   # C-
        (54, 1.33),   # D+
        (50, 1.00),   # D
    ]

    #: Relative grading z-score bands. (minimum z, grade point).
    RELATIVE_BANDS = [
        (2.00, 4.00),    # A+
        (1.50, 4.00),    # A
        (1.00, 3.67),    # A-
        (0.50, 3.33),    # B+
        (-0.50, 3.00),   # B — the mean sits here
        (-1.00, 2.67),   # B-
        (-1.33, 2.33),   # C+
        (-1.67, 2.00),   # C
        (-2.00, 1.67),   # C-
    ]

    #: At or beyond -2SD is still a pass, provided the mark clears the
    #: threshold. The handbook groups "D & D+" into this one band without
    #: splitting them, so the lower of the two is awarded.
    LOWEST_PASSING_GRADE_POINT = 1.00

    def _grade_point(self, bands, value, floor):
        for minimum, grade_point in bands:
            if value >= minimum:
                return grade_point
        return floor

    def calculate_gpa(self, data, passing_threshold=None):
        threshold = passing_threshold or self.DEFAULT_PASSING_THRESHOLD
        results = Result.objects.filter(enrollment__in=data.keys())
        final_result_data = {}

        marked = {e: m for e, m in data.items() if m is not None}
        # Failing marks are excluded from the statistics, not just graded 0.
        passing = {e: m for e, m in marked.items() if m >= threshold}
        failing = {e: m for e, m in marked.items() if m < threshold}

        def record(enrollment, obtained, course_gpa, extra=None):
            entry = {'obtained': obtained, 'course_gpa': course_gpa}
            if extra:
                entry.update(extra)
            final_result_data[enrollment.student.student_id] = entry
            student_result = results.get(enrollment=enrollment)
            student_result.obtained_marks = obtained
            student_result.course_gpa = course_gpa
            student_result.save()

        for enrollment, obtained in failing.items():
            record(enrollment, obtained, 0.0)

        if not passing:
            return final_result_data

        # Class size is the whole class, not just those who passed.
        if len(marked) < self.RELATIVE_GRADING_MIN_CLASS_SIZE:
            for enrollment, obtained in passing.items():
                record(enrollment, obtained,
                       self._grade_point(self.ABSOLUTE_BANDS, obtained, 0.0))
            return final_result_data

        values = list(passing.values())
        mean = statistics.mean(values)
        standard_deviation = statistics.pstdev(values)
        final_result_data['mean'] = mean
        final_result_data['standard_deviation'] = standard_deviation

        for enrollment, obtained in passing.items():
            score = (obtained - mean) / standard_deviation if standard_deviation else 0.0
            record(
                enrollment, obtained,
                self._grade_point(self.RELATIVE_BANDS, score,
                                  self.LOWEST_PASSING_GRADE_POINT),
                {'score': score},
            )

        return final_result_data

    def calculate_result(self, instance):
        results = {}
        if isinstance(instance, CourseAllocation):
            enrollments = Enrollment.objects.filter(allocation=instance).prefetch_related('assessmentchecked_set')
            for each_enrollment in list(enrollments):
                if each_enrollment.assessmentchecked_set.exists():
                    student_result = Decimal('0.00')
                    for each_assessment in each_enrollment.assessmentchecked_set.all():
                        # A mark never entered counts as zero. The faculty path
                        # refuses null marks before it ever reaches here; this
                        # matters for the automatic calculation at closing,
                        # where nobody is left who could fill them in.
                        obtained = each_assessment.obtained or Decimal('0.00')
                        student_result += ((
                                                       obtained / each_assessment.assessment.total_marks) * each_assessment.assessment.weightage)

                    results[each_enrollment] = student_result
            return self.calculate_gpa(results, instance.passing_threshold)
        else:
            return {'message': 'Valid course allocation instance not provided.'}



class TranscriptGenerationMixin:
    """Builds a semester's transcripts from the results on record.

    Shared by the manual admin endpoint and by semester_closing_task, which
    generates transcripts *before* cascading statuses — a semester already
    marked Completed can no longer have transcripts made for it.
    """

    def generate_transcripts(self, semester):
        """Create one Transcript per student for `semester`.

        Returns the created rows. Raises ValueError, with a per-student map,
        if any enrollment is still missing a result — the caller decides
        whether that is a validation error or a task failure.

        Only 'Locked' enrollments count. Dropped and inactive ones are not part
        of a GPA, and including them used to block a student's whole transcript
        over a course they had dropped.
        """
        if Transcript.objects.filter(semester=semester).exists():
            logger.info(
                'Transcripts already exist for semester_id=%s, nothing to do',
                semester.semester_id,
            )
            return []

        student_list = Student.objects.filter(
            enrollment__allocation__semester=semester,
            enrollment__status='Locked',
        ).distinct().prefetch_related(
            Prefetch(
                'enrollment_set',
                queryset=Enrollment.objects.filter(
                    allocation__semester=semester, status='Locked',
                ).select_related('allocation__course', 'result'),
            )
        )

        errors = {}
        for student in student_list:
            for enrollment in student.enrollment_set.all():
                result = getattr(enrollment, 'result', None)
                if not result or result.course_gpa is None:
                    errors[student.student_id.person_id] = (
                        f'Result does not exist for enrollment {enrollment.enrollment_id}'
                    )
        if errors:
            raise ValueError(errors)

        rows = []
        skipped = []
        for student in student_list:
            enrollments = list(student.enrollment_set.all())
            if not enrollments:
                continue

            total_credits = sum(e.allocation.course.credit_hours for e in enrollments)
            if not total_credits:
                # Every course this student took carries zero credit hours, so
                # there is no weighted average to compute. Skip them rather
                # than abort — one mis-configured course must not stop a
                # semester from closing — but make it visible to an admin,
                # since a missing transcript is otherwise silent.
                logger.warning(
                    'Skipping transcript for student %s in semester_id=%s: zero credit hours',
                    student.student_id.person_id, semester.semester_id,
                )
                skipped.append(student)
                continue

            weighted = sum(
                e.result.course_gpa * e.allocation.course.credit_hours for e in enrollments
            )
            rows.append(Transcript(
                student=student,
                semester=semester,
                total_credits=total_credits,
                semester_gpa=weighted / total_credits,
            ))

        transcripts = Transcript.objects.bulk_create(rows)
        logger.info(
            'Generated %s transcript(s) for semester_id=%s',
            len(transcripts), semester.semester_id,
        )

        if skipped:
            self._notify_skipped_transcripts(semester, skipped)

        return transcripts

    def _notify_skipped_transcripts(self, semester, skipped):
        """Tell every active admin which students got no transcript, and why."""
        names = ', '.join(s.student_id.person_id for s in skipped)
        semester_type = ContentType.objects.get_for_model(Semester)

        for admin in Admin.objects.filter(status='Active').select_related('employee_id__user'):
            if not admin.employee_id.user_id:
                continue
            Notification.objects.create(
                recipient=admin.employee_id.user,
                verb='transcript_skipped',
                message=(
                    f'No transcript generated for {len(skipped)} student(s) in '
                    f'{semester}: all their courses carry zero credit hours ({names}). '
                    f'Fix the course credit hours and regenerate.'
                )[:255],
                level='action_required',
                content_type=semester_type,
                object_id=semester.pk,
            )


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

