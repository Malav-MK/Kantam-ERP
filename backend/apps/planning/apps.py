from django.apps import AppConfig


class PlanningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.planning"

    def ready(self):
        from importlib import import_module
        from importlib.util import find_spec

        for module in ("resources", "signals"):
            if find_spec(f"{self.name}.{module}"):
                import_module(f"{self.name}.{module}")
