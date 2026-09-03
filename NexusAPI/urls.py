"""
URL configuration for NexusAPI project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import io
import logging

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse, JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

logger = logging.getLogger(__name__)


class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView
)

from django.views.generic import TemplateView

from AdminModule.views import CurrentSessionView


@staff_member_required
def seed_demo_data_view(request):
    """Runs the seed_demo_data management command and shows what it did.
    Staff-only (redirects anonymous visitors to login) and idempotent —
    safe to click more than once."""
    out = io.StringIO()
    call_command('seed_demo_data', stdout=out)
    log = out.getvalue().strip() or 'Nothing to do — demo data already meets the target counts.'

    body = "\n".join(f"<div>{line}</div>" for line in log.splitlines())
    html = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Seed Demo Data — NexusAPI</title>
      <style>
        body {{ background:#07070e; color:#eeedf5; font-family:'DM Mono',monospace;
                padding:2.5rem; line-height:1.7; }}
        h1 {{ font-family:'Syne',sans-serif; color:#93edd9; font-size:1.4rem; }}
        .log {{ background:#0f0f1a; border:1px solid rgba(255,255,255,0.1); border-radius:10px;
                padding:1.2rem 1.4rem; margin:1.2rem 0; white-space:pre-wrap; font-size:0.85rem; }}
        a {{ color:#7c6fff; }}
      </style>
    </head>
    <body>
      <h1>Demo data seeded</h1>
      <div class="log">{body}</div>
      <a href="/">&larr; Back to home</a>
    </body>
    </html>
    """
    return HttpResponse(html)


def health_check_view(request):
    """Unauthenticated liveness/readiness probe for uptime monitoring.
    DB and cache checks are synchronous and determine the overall status;
    Celery worker responsiveness is best-effort (short timeout) and reported
    for visibility only — a slow/absent worker degrades background task
    processing, not the API's ability to serve this very request."""
    checks = {}
    healthy = True

    try:
        connection.ensure_connection()
        checks['database'] = 'ok'
    except OperationalError as e:
        checks['database'] = f'error: {e}'
        healthy = False
        logger.error('Health check: database unreachable: %s', e)

    try:
        cache.set('health_check', '1', timeout=5)
        if cache.get('health_check') == '1':
            checks['cache'] = 'ok'
        else:
            checks['cache'] = 'error: unexpected value on read-back'
            healthy = False
    except Exception as e:
        checks['cache'] = f'error: {e}'
        healthy = False
        logger.error('Health check: cache unreachable: %s', e)

    try:
        from NexusAPI.celery import app
        pings = app.control.inspect(timeout=1.0).ping() or {}
        checks['celery'] = f'{len(pings)} worker(s) responding' if pings else 'no workers responding'
    except Exception as e:
        checks['celery'] = f'error: {e}'

    return JsonResponse(
        {'status': 'ok' if healthy else 'degraded', **checks},
        status=200 if healthy else 503,
    )


urlpatterns = [
        # landing page
        path('', TemplateView.as_view(template_name='index.html'), name='home'),
        # django admin site
        path('admin/', admin.site.urls),
        # demo data seeding (staff-only)
        path('seed-demo-data/', seed_demo_data_view, name='seed-demo-data'),
        # uptime monitoring probe
        path('health/', health_check_view, name='health-check'),

        # JWT Authentication
        path('api/token/', LoginView.as_view(), name='token_obtain_pair'),
        path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
        # Public — current live session phase, needed pre-login
        path('api/sessions/current/', CurrentSessionView.as_view(), name='session-current'),
        # Accounts Login
        path('accounts/', include('django.contrib.auth.urls')),

        # Project urls
        path('api/admin/', include('AdminModule.urls', namespace='Admin')),
        path('api/faculty/', include('FacultyModule.urls')),
        path('api/student/', include('StudentModule.urls')),
        path('api/notifications/', include('NotificationModule.urls')),

        # API Documentation
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
