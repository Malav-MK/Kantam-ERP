from django.apps import AppConfig


class ProductionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production"

    def ready(self):
        from importlib import import_module
        from importlib.util import find_spec

        for module in ("resources", "signals"):
            if find_spec(f"{self.name}.{module}"):
                import_module(f"{self.name}.{module}")
