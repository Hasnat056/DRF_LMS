"""
email_service.py
-----------------
Every outbound email AdminModule sends, as a plain function.

Tasks, serializers and mixins call in here rather than composing messages
inline, so the wording of a notice lives in one place and the surrounding
logic stays readable. These are ordinary functions, not Celery tasks — the
task wrappers stay in tasks.py so their registered names never move.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

SIGNATURE = "Thank you,\nMeridian Institute of Technology"


# ---------------------------------------------------------------------------
# Record deletion
# ---------------------------------------------------------------------------

def send_delete_request(person, confirmation_link):
    """Ask a person to confirm deletion of their own record."""
    send_mail(
        subject=f"Delete Request : {person.person_id}",
        message=(
            f"Dear {person.first_name} {person.last_name},\n"
            f"A request has been made to delete the record of {person.person_id} "
            f"from the system. This action will permanently remove all related "
            f"data and cannot be undone.\n"
            f"If you requested this change, please confirm by clicking the link below:\n"
            f"Confirmation link : {confirmation_link} \n"
            f"The links will expire in 48 hours.\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[person.institutional_email],
    )
    return 'Email sent successfully'


# ---------------------------------------------------------------------------
# Head of Department
# ---------------------------------------------------------------------------

def send_hod_request(change_request, confirmation_link):
    """Invite a faculty member to accept the HOD role."""
    faculty = change_request.new_hod
    send_mail(
        subject=f"HOD Change Request : {faculty.employee_id}",
        message=(
            f"Dear {faculty.employee_id.first_name} {faculty.employee_id.last_name},\n"
            f"You have been requested to appoint as the new Head of Department "
            f"for the {change_request.department.department_name}\n"
            f"If you are willing to uphold this responsibility, please confirm "
            f"by clicking the link below:\n"
            f"Confirmation link : {confirmation_link} \n"
            f"The links will expire in 48 hours.\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[faculty.employee_id.institutional_email],
    )
    return 'Email sent successfully'


def send_hod_appointment(change_request, old_hod=None):
    """Congratulate the incoming HOD, and thank the outgoing one if there is one."""
    new_hod = change_request.new_hod
    send_mail(
        subject=f"HOD Appointment : {new_hod}",
        message=(
            f"Dear {new_hod.employee_id.first_name} {new_hod.employee_id.last_name},\n"
            f"Congratulations! You have been appointed as the new Head of Department "
            f"for the {change_request.department.department_name}\n"
            f"Looking forward to your contributions for the welfare of the department\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[new_hod.employee_id.institutional_email],
    )

    if old_hod is not None:
        successor = change_request.department.HOD.employee_id
        send_mail(
            subject=f"HOD Change : {old_hod.employee_id}",
            message=(
                f"Dear {old_hod.employee_id.first_name} {old_hod.employee_id.last_name},\n"
                f"Your position as the Head of department for the "
                f"{change_request.department.department_name} has been transferred to "
                f"Mr. {successor.first_name} {successor.last_name}\n"
                f"We thankyou for you services and contributions to the welfare "
                f"of the department \n\n"
                + SIGNATURE
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[old_hod.employee_id.institutional_email],
        )

    return 'Emails sent successfully'


# ---------------------------------------------------------------------------
# Result calculation
# ---------------------------------------------------------------------------

def send_result_calculation_request(change_request, confirmation_link, recipient_email):
    """Ask an admin to approve a faculty member's result calculation request."""
    allocation = change_request.target_allocation
    send_mail(
        subject=f"Result Calculation Request : {allocation.allocation_id}",
        message=(
            f"Dear Admin,\n"
            f"A result calculation request has been made for the course allocation: \n"
            f"Course Allocation ID: {allocation.allocation_id}\n"
            f"Faculty ID: {allocation.faculty.employee_id.person_id}\n"
            f"Faculty Name: {allocation.faculty.employee_id.first_name} "
            f"{allocation.faculty.employee_id.last_name}\n"
            f"Semester ID: {allocation.semester.semester_id}\n"
            f"Session: {allocation.session}\n"
            f"To approve this request click the link below:\n"
            f"Confirmation link : {confirmation_link} \n"
            f"The links will expire in 48 hours.\n\n"
            + SIGNATURE
        ),
        from_email=change_request.requested_by.username,
        recipient_list=[recipient_email],
    )
    return 'Emails sent successfully'


def send_result_calculation_approved(change_request):
    """Tell the faculty member their calculation request was approved."""
    allocation = change_request.target_allocation
    send_mail(
        subject="Result Calculation Request Approved",
        message=(
            f"Dear Faculty member,\n"
            f"Your request to calculate the result for the course allocation: \n"
            f"Course Allocation ID: {allocation.allocation_id}\n"
            f"Faculty ID: {allocation.faculty.employee_id.person_id}\n"
            f"Faculty Name: {allocation.faculty.employee_id.first_name} "
            f"{allocation.faculty.employee_id.last_name}\n"
            f"Semester ID: {allocation.semester_id}\n"
            f"Session: {allocation.session}\n"
            f"has been approved by the admin. Kindly visit your portal to apply changes\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[allocation.faculty.employee_id.institutional_email],
    )
    return 'Emails sent successfully'


# ---------------------------------------------------------------------------
# Closing-deadline reminders
# ---------------------------------------------------------------------------

def send_pending_results_to_admin(session, pending, recipient_email, remaining):
    """Warn an admin which allocations still have no results as the session's
    closing deadline approaches.

    `pending` is a list of (allocation, missing_count) pairs; `remaining` is a
    human phrase such as "2 days".
    """
    lines = [
        f"  - Allocation {allocation.allocation_id} "
        f"({allocation.course.course_code}, {allocation.semester}) — "
        f"{missing} enrollment(s) without a result, "
        f"faculty: {allocation.faculty.employee_id.first_name} "
        f"{allocation.faculty.employee_id.last_name} "
        f"({allocation.faculty.employee_id.institutional_email})"
        for allocation, missing in pending
    ]

    send_mail(
        subject=f"Results pending for {session} — closing in {remaining}",
        message=(
            f"Dear Admin,\n\n"
            f"The session {session} closes in {remaining} "
            f"({session.closing_deadline:%Y-%m-%d %H:%M}).\n"
            f"The following course allocations still have enrollments without "
            f"a calculated result:\n\n"
            + "\n".join(lines)
            + "\n\nAny result still missing when the session closes will be "
              "calculated automatically from the marks on record, and "
              "transcripts will be generated before the semester is closed.\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )
    logger.info(
        'Pending-results notice sent to admin %s for session_id=%s (%s allocation(s), %s left)',
        recipient_email, session.id, len(pending), remaining,
    )
    return 'Email sent successfully'


def send_pending_results_to_faculty(session, allocation, missing, remaining):
    """Warn a faculty member that one of their allocations has no results yet."""
    send_mail(
        subject=f"Result calculation pending — {allocation.course.course_code} ({allocation.semester})",
        message=(
            f"Dear {allocation.faculty.employee_id.first_name} "
            f"{allocation.faculty.employee_id.last_name},\n\n"
            f"The session {session} closes in {remaining} "
            f"({session.closing_deadline:%Y-%m-%d %H:%M}).\n\n"
            f"Course Allocation ID: {allocation.allocation_id}\n"
            f"Course: {allocation.course.course_code} - {allocation.course.course_name}\n"
            f"Semester: {allocation.semester}\n"
            f"Enrollments without a result: {missing}\n\n"
            f"Kindly calculate the result from your portal before the deadline. "
            f"You may adjust the passing threshold for this allocation and "
            f"recalculate as needed.\n\n"
            f"If the deadline passes, results will be calculated automatically "
            f"from the marks on record.\n\n"
            + SIGNATURE
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[allocation.faculty.employee_id.institutional_email],
    )
    logger.info(
        'Pending-results notice sent to faculty for allocation_id=%s (%s missing, %s left)',
        allocation.allocation_id, missing, remaining,
    )
    return 'Email sent successfully'
