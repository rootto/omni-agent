import os
from dotenv import load_dotenv

load_dotenv()

def get_gcs_bucket_name() -> str:
    bucket = os.environ.get("GCS_BUCKET_NAME")
    if not bucket:
        raise ValueError("GCS_BUCKET_NAME environment variable is not set. Please specify it via .env or environment.")
    return bucket
