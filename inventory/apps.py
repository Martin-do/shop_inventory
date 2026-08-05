from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"

    def ready(self):
        from . import stocktake_models  # noqa: F401
        from . import audit_signals  # noqa: F401

        import sys
        if any(cmd in sys.argv for cmd in ['test', 'makemigrations', 'migrate', 'collectstatic']):
            return

        import os
        import threading
        if os.environ.get('RUN_MAIN') == 'true' or 'runserver' not in sys.argv:
            thread = threading.Thread(target=self.start_background_backup_scheduler, daemon=True)
            thread.start()

    def start_background_backup_scheduler(self):
        import time
        time.sleep(10)

        while True:
            try:
                from .models import StoreSettings, BackupLog
                from .backup_utils import run_backup_job
                from django.utils import timezone
                from datetime import timedelta

                settings = StoreSettings.get_solo()
                if settings.auto_backup_enabled:
                    last_success = BackupLog.objects.filter(status="success").order_by("-timestamp").first()
                    should_run = not last_success
                    if last_success:
                        elapsed = timezone.now() - last_success.timestamp
                        should_run = elapsed >= timedelta(hours=settings.auto_backup_interval_hours)
                    if should_run:
                        run_backup_job()
            except Exception:
                pass

            time.sleep(3600)
