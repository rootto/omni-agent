import os

def get_gcs_bucket_name() -> str:
    return os.environ.get("GCS_BUCKET_NAME", "test-omini-bucket123")
