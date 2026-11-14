from django.apps import AppConfig


class TimersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "timers"

    def ready(self):
        from . import worker

        if worker.should_start():
            worker.start()
