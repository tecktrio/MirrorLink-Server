import os
from pathlib import Path
from dotenv import load_dotenv
import mimetypes
import urllib.parse
import boto3
from botocore.exceptions import ClientError

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(os.path.join(BASE_DIR, ".env"))

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME") or os.environ.get("AWS_S3_BUCKET_NAME")
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME") or os.environ.get("AWS_REGION", "ap-southeast-2")
AWS_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN")

_s3_client = None

def get_s3_client():
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        try:
            _s3_client = boto3.client(
                's3',
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_S3_REGION_NAME
            )
            return _s3_client
        except Exception as e:
            print(f"[AWS S3] Client initialization error: {e}")
            return None
    return None

def is_s3_configured():
    return bool(AWS_STORAGE_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)

def upload_file_to_s3(file_path, object_name=None, content_type=None):
    """
    Upload a local file to AWS S3 bucket and return its public URL.
    Returns None if S3 is not configured or upload fails.
    """
    if not is_s3_configured():
        print("[AWS S3] Notice: S3 credentials not fully configured, falling back to local storage.")
        return None

    client = get_s3_client()
    if not client:
        return None

    if object_name is None:
        object_name = f"contents/{os.path.basename(file_path)}"

    if not content_type:
        content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "application/octet-stream"

    extra_args = {
        'ContentType': content_type
    }

    try:
        print(f"[AWS S3] Uploading {file_path} to s3://{AWS_STORAGE_BUCKET_NAME}/{object_name}...")
        client.upload_file(file_path, AWS_STORAGE_BUCKET_NAME, object_name, ExtraArgs=extra_args)

        if AWS_S3_CUSTOM_DOMAIN:
            url = f"https://{AWS_S3_CUSTOM_DOMAIN}/{object_name}"
        else:
            url = f"https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/{object_name}"

        print(f"[AWS S3] Upload complete: {url}")
        return url
    except ClientError as e:
        print(f"[AWS S3] Upload error: {e}")
        return None
    except Exception as e:
        print(f"[AWS S3] Unexpected error: {e}")
        return None

def delete_file_from_s3(url_or_key):
    """
    Deletes an object from the configured AWS S3 bucket using its URL or object key.
    """
    if not is_s3_configured() or not url_or_key:
        return False

    client = get_s3_client()
    if not client:
        return False

    try:
        parsed = urllib.parse.urlparse(url_or_key)
        if parsed.scheme and parsed.netloc:
            # Strip leading slash
            key = parsed.path.lstrip('/')
        else:
            key = url_or_key

        print(f"[AWS S3] Deleting object '{key}' from bucket '{AWS_STORAGE_BUCKET_NAME}'...")
        client.delete_object(Bucket=AWS_STORAGE_BUCKET_NAME, Key=key)
        return True
    except Exception as e:
        print(f"[AWS S3] Delete error: {e}")
        return False
