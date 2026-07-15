from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"

    def ready(self):
        import sys
        # Avoid starting thread during tests, migrations, or database commands
        if any(cmd in sys.argv for cmd in ['test', 'makemigrations', 'migrate', 'collectstatic']):
            return

        import os
        import threading
        # Ensure thread starts only once if runserver reloader is active
        if os.environ.get('RUN_MAIN') == 'true' or 'runserver' not in sys.argv:
            thread = threading.Thread(target=self.start_background_backup_scheduler, daemon=True)
            thread.start()

    def start_background_backup_scheduler(self):
        import time
        # Let server boot finish
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
                    
                    should_run = False
                    if not last_success:
                        should_run = True
                    else:
                        elapsed = timezone.now() - last_success.timestamp
                        if elapsed >= timedelta(hours=settings.auto_backup_interval_hours):
                            should_run = True
                            
                    if should_run:
                        run_backup_job()
            except Exception:
                pass
                
            # Check once every hour
            time.sleep(3600)
