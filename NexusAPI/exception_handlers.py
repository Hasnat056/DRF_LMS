"""
exception_handlers.py
----------------------
Translates database-level rule violations into ordinary API validation errors.

The session deadline windows are enforced by MySQL triggers as well as by the
serializers (see Models/migrations/0019). A trigger rejection arrives as
``OperationalError(1644, 'message')`` — MySQL's code for a SIGNAL raised in a
trigger — which DRF would otherwise let through as a 500.

Catching it here rather than in a model manager is deliberate: managers only
see ``save()``. ``queryset.update()``, ``bulk_update()`` and raw SQL all reach
the trigger without passing through one, and every path should produce the same
response.
"""
import logging

from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

# MySQL: "unhandled user-defined exception condition" — what SIGNAL SQLSTATE
# '45000' raises. Anything else from OperationalError is a genuine database
# fault and must keep bubbling up as a 500.
MYSQL_SIGNAL_ERROR = 1644


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, OperationalError) and exc.args and exc.args[0] == MYSQL_SIGNAL_ERROR:
        message = exc.args[1] if len(exc.args) > 1 else 'Database rule violation.'
        logger.warning(
            'Database rule rejected a write in %s: %s',
            context.get('view').__class__.__name__ if context.get('view') else 'unknown view',
            message,
        )
        return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

    return response
