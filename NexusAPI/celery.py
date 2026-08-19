from __future__ import absolute_import, unicode_literals
import os
from datetime import timedelta
from celery import Celery

# tell celery where django settings are located
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'NexusAPI.settings')

# creating a celery instance
app = Celery('DJANGO_RESTProject_practice')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()

app.conf.beat_schedule = {
    'reconcile-lifecycle-states': {
        'task': 'AdminModule.tasks.reconcile_lifecycle_states',
        'schedule': timedelta(minutes=10),
    },
}