# firebase_config.py

import re
import firebase_admin
from firebase_admin import credentials, storage
import urllib.parse

def configure():
    # Initialize Firebase app with credentials
    cred = credentials.Certificate('firebase_key.json')
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'mirrorlink-22549.appspot.com'
    })



def delete_file_from_public_url(public_url):
    # Parse the public URL to get the bucket name and file path
    # Example public URL: "https://storage.googleapis.com/mirrorlink-22549.appspot.com/contents/test%20content"

    # Split the URL to extract the bucket name and file path
    parsed_url = urllib.parse.urlparse(public_url)
 # 'storage.googleapis.com' should be used correctly
    file_path = parsed_url.path.lstrip('/')  # 'mirrorlink-22549.appspot.com/contents/test content'
 # Get the bucket name from the path
    file_path = '/'.join(file_path.split('/')[1:])  # Remaining path
    # print(file_path)
        
    # file_path = parsed_url.path.lstrip('/')  # Remove the leading slash

    # Decode the URL-encoded spaces and other characters
    decoded_file_path = urllib.parse.unquote(file_path)

    # print(decoded_file_path)
    # Initialize a client
    bucket = storage.bucket()


    # Get the bucket
    # bucket = client.get_bucket(bucket_name)

    # Get the blob (file) using the file path
    blob = bucket.blob(decoded_file_path)
    # print('working', file_path)

    # Delete the blob
    blob.delete()


    # print(f'File {public_url} has been deleted.')