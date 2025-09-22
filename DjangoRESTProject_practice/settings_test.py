from .settings import *
import os

print(">>> USING TEST SETTINGS <<<")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, "test_db.sqlite3"),
        'TEST': {
            'NAME': os.path.join(BASE_DIR, "test_db.sqlite3"),  # <---- Key Fix
        },
    }
}

# Optional: Disable migrations for faster tests
MIGRATION_MODULES = {
    app: None for app in INSTALLED_APPS
}
