"""Turn Course.lab from a boolean into the lab course itself.

The old boolean meant "this course has a lab", and the serializer folded the
lab's credit hour into the theory course. Labs are now real Course rows
({code}-L, one credit hour) that are allocated, enrolled in and graded on
their own, so that hour moves off the theory course and onto the lab.

Every step here is written to degrade rather than abort: a row that cannot be
converted is logged and skipped, never raised. A migration that stops halfway
through a production deploy is worse than one that leaves a handful of
courses for an admin to finish by hand.
"""

import logging

import django.db.models.deletion
from django.db import migrations, models

logger = logging.getLogger(__name__)

LAB_CODE_SUFFIX = '-L'
LAB_NAME_SUFFIX = '-Lab'
MAX_CODE_LENGTH = 20
MAX_NAME_LENGTH = 100


def split_lab_courses(apps, schema_editor):
    """Give every has_lab course a {code}-L row and take its hour back."""
    Course = apps.get_model('Models', 'Course')

    converted = skipped = 0
    for course in Course.objects.filter(has_lab=True).order_by('course_code'):
        lab_code = f'{course.course_code}{LAB_CODE_SUFFIX}'

        if len(lab_code) > MAX_CODE_LENGTH:
            logger.warning(
                'Course %s: no room for a %s suffix within %s characters, '
                'leaving it unconverted',
                course.course_code, LAB_CODE_SUFFIX, MAX_CODE_LENGTH,
            )
            skipped += 1
            continue

        lab = Course.objects.filter(course_code=lab_code).first()
        if lab is None:
            lab = Course.objects.create(
                course_code=lab_code,
                course_name=f'{course.course_name}{LAB_NAME_SUFFIX}'[:MAX_NAME_LENGTH],
                credit_hours=1,
            )
        elif Course.objects.filter(lab=lab).exclude(pk=course.pk).exists():
            # Someone else already claims that row as their lab. Linking it
            # would trip the one-to-one constraint, so leave both alone.
            logger.warning(
                'Course %s: %s is already the lab of another course, '
                'leaving it unconverted',
                course.course_code, lab_code,
            )
            skipped += 1
            continue

        course.lab = lab
        # The boolean folded the lab's hour into the theory course; it lives
        # on the lab row now. Floor at zero -- credit_hours is a positive
        # field, and a mis-configured 0-hour course must not stop the deploy.
        course.credit_hours = max(course.credit_hours - 1, 0)
        course.save(update_fields=['lab', 'credit_hours'])
        converted += 1

    logger.info(
        'Lab split complete: %s course(s) converted, %s skipped', converted, skipped
    )


def merge_lab_courses(apps, schema_editor):
    """Reverse of the above: fold each lab back into its theory course."""
    Course = apps.get_model('Models', 'Course')

    for course in Course.objects.filter(lab__isnull=False).order_by('course_code'):
        lab = course.lab

        course.has_lab = True
        course.credit_hours = course.credit_hours + (lab.credit_hours or 0)
        course.lab = None
        course.save(update_fields=['has_lab', 'credit_hours', 'lab'])

        # Only drop rows this migration would have created, and only while
        # nothing points at them -- an allocated or scheduled lab is real
        # data an admin has since built on.
        if not lab.course_code.endswith(LAB_CODE_SUFFIX):
            continue
        if lab.courseallocation_set.exists() or lab.semesterdetails_set.exists():
            logger.warning(
                'Lab %s is in use, keeping it as a standalone course', lab.course_code
            )
            continue
        lab.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('Models', '0020_alter_admin_options_alter_courseallocation_options_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='course',
            old_name='lab',
            new_name='has_lab',
        ),
        migrations.AddField(
            model_name='course',
            name='lab',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='lab_for',
                to='Models.course',
            ),
        ),
        migrations.AlterField(
            model_name='course',
            name='pre_requisite',
            field=models.ForeignKey(
                blank=True,
                db_column='preRequisite',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='dependent_courses',
                to='Models.course',
            ),
        ),
        migrations.RunPython(split_lab_courses, merge_lab_courses),
        migrations.RemoveField(
            model_name='course',
            name='has_lab',
        ),
    ]
