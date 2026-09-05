"""Database-level enforcement of the session deadline windows.

The whole lifecycle hangs off AcademicSession.activation_deadline and
.closing_deadline, and every write path outside the API — data migrations,
management commands, `manage.py shell`, bulk updates — bypasses the serializer
validators entirely. These triggers hold the same rules at the database.

Triggers rather than CHECK constraints because the rules are relative to "now",
and MySQL rejects non-deterministic functions inside a CHECK (error 3814). A
clock-dependent CHECK would also be re-evaluated on table rebuilds and dump
restores, failing on rows that were perfectly valid when written.

Note on direction: the minimum-window rules only fire when the new value is in
the future. Writing a past deadline is recording history, not scheduling, so
"must be 2 weeks ahead" has nothing to say about it. The maximum applies
always — a date years out is wrong however it got there.
"""
from django.db import migrations


ACTIVATION_CHECKS = """
    IF NEW.activation_deadline IS NOT NULL THEN
        IF NEW.activation_deadline > NOW()
           AND NEW.activation_deadline < NOW() + INTERVAL 2 WEEK THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Activation deadline must be at least 2 weeks ahead';
        END IF;
        IF NEW.activation_deadline > NOW() + INTERVAL 4 WEEK THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Activation deadline cannot be more than 4 weeks ahead';
        END IF;
    END IF;
"""

CLOSING_CHECKS = """
    IF NEW.closing_deadline IS NOT NULL THEN
        IF NEW.closing_deadline > NOW()
           AND NEW.closing_deadline < NOW() + INTERVAL 1 WEEK THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Closing deadline must be at least 1 week ahead';
        END IF;
        IF NEW.closing_deadline > NOW() + INTERVAL 4 WEEK THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Closing deadline cannot be more than 4 weeks ahead';
        END IF;
    END IF;
"""

CREATE_INSERT_TRIGGER = f"""
CREATE TRIGGER academicsession_deadlines_before_insert
BEFORE INSERT ON Models_academicsession
FOR EACH ROW
BEGIN
    {ACTIVATION_CHECKS}
    {CLOSING_CHECKS}
END
"""

# On update the same window rules apply, plus two that only make sense as a
# transition: a closing deadline may not be cleared once set, and the
# availability delta is frozen unless a pending activation deadline exists.
CREATE_UPDATE_TRIGGER = f"""
CREATE TRIGGER academicsession_deadlines_before_update
BEFORE UPDATE ON Models_academicsession
FOR EACH ROW
BEGIN
    {ACTIVATION_CHECKS}
    {CLOSING_CHECKS}

    IF OLD.closing_deadline IS NOT NULL AND NEW.closing_deadline IS NULL THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Closing deadline cannot be cleared.';
    END IF;

    IF NEW.availability_delta <> OLD.availability_delta THEN
        IF NEW.activation_deadline IS NULL THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Availability delta cannot be set until the session has an activation deadline.';
        END IF;
        IF NEW.activation_deadline <= NOW() THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Availability delta cannot be changed once the activation deadline has passed.';
        END IF;
        IF NEW.activation_deadline - INTERVAL NEW.availability_delta DAY <= NOW() THEN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Availability window would open before now.';
        END IF;
    END IF;
END
"""


class Migration(migrations.Migration):

    dependencies = [
        ('Models', '0018_academicsession_closing_deadline_after_activation_deadline'),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_INSERT_TRIGGER,
            reverse_sql='DROP TRIGGER IF EXISTS academicsession_deadlines_before_insert',
        ),
        migrations.RunSQL(
            sql=CREATE_UPDATE_TRIGGER,
            reverse_sql='DROP TRIGGER IF EXISTS academicsession_deadlines_before_update',
        ),
    ]
