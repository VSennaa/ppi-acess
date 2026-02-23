import os

from django.apps import AppConfig


class AlprAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "alpr_app"

    def ready(self):
        # Evita duplicar thread no autoreload do runserver.
        if os.environ.get("RUN_MAIN") != "true":
            return

        from alpr_app.services.recognition_worker import worker

        worker.start()
