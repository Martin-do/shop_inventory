import json
import os
import sqlite3
import tempfile
import zipfile

from django.conf import settings
from django.utils import timezone
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .models import BackupLog, StoreSettings


def backup_sqlite(dst_path):
    """Safely back up the active sqlite database using SQLite's backup API to avoid lock contention."""
    src_conn = sqlite3.connect(settings.DATABASES["default"]["NAME"])
    dst_conn = sqlite3.connect(dst_path)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()


def create_backup_zip():
    """Create a zip file containing a safe copy of the sqlite database and the media directory."""
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"backup_{timestamp}.zip"

    temp_dir = tempfile.gettempdir()
    zip_path = os.path.join(temp_dir, zip_filename)

    db_copy_path = os.path.join(temp_dir, "shop_inventory.sqlite3")
    if os.path.exists(db_copy_path):
        try:
            os.remove(db_copy_path)
        except Exception:
            pass

    backup_sqlite(db_copy_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add database copy to zip
        zip_file.write(db_copy_path, arcname="shop_inventory.sqlite3")

        # Add media folder to zip if it exists
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for root, dirs, files in os.walk(media_root):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Preserve relative structure inside the zip's media folder
                    arcname = os.path.join("media", os.path.relpath(file_path, media_root))
                    zip_file.write(file_path, arcname=arcname)

    # Clean up the DB copy
    if os.path.exists(db_copy_path):
        try:
            os.remove(db_copy_path)
        except Exception:
            pass

    return zip_path, zip_filename


def upload_to_google_drive(file_path, filename, folder_id, service_account_json):
    """Upload backup zip to Google Drive using Google service account credentials."""
    creds_info = json.loads(service_account_json)
    credentials = Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/drive.file"]
    )

    service = build("drive", "v3", credentials=credentials)

    file_metadata = {
        "name": filename,
    }
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(file_path, mimetype="application/zip", resumable=True)

    file = service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()

    return file.get("id")


def run_backup_job():
    """Create a backup of the POS database/media and upload it to Google Drive, logging the result."""
    settings_obj = StoreSettings.get_solo()

    if not settings_obj.google_service_account_json or not settings_obj.google_drive_folder_id:
        # Log failure
        BackupLog.objects.create(
            status="failed",
            file_name="backup_attempt.zip",
            error_message="Google Service Account credentials or Folder ID not configured in Settings."
        )
        return False, "Google Backup credentials not configured."

    zip_path = None
    try:
        zip_path, zip_filename = create_backup_zip()
        file_size = os.path.getsize(zip_path)

        # Upload to Google Drive
        drive_file_id = upload_to_google_drive(
            zip_path,
            zip_filename,
            settings_obj.google_drive_folder_id,
            settings_obj.google_service_account_json
        )

        # Log success
        BackupLog.objects.create(
            status="success",
            file_name=zip_filename,
            file_size_bytes=file_size,
        )
        return True, f"Backup successfully uploaded to Google Drive. File ID: {drive_file_id}"

    except Exception as e:
        # Log failure
        BackupLog.objects.create(
            status="failed",
            file_name=zip_filename if "zip_filename" in locals() and zip_filename else "backup_failed.zip",
            error_message=str(e)
        )
        return False, f"Backup failed: {str(e)}"

    finally:
        # Clean up local temp zip file
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
