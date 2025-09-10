from __future__ import absolute_import, unicode_literals
import os
from celery import Celery

# tell celery where django settings are located
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'DjangoRESTProject_practice.settings')

# creating a celery instance
app = Celery('DJANGO_RESTProject_practice')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()