"""
Download stock minute bars flat files from S3
"""
import boto3
from botocore.config import Config
import os

S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

DATA_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data\\stocks"

s3 = boto3.Session(
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
).client('s3', endpoint_url=S3_ENDPOINT, config=Config(signature_version='s3v4'))

# Check what's available
print("Checking stock minute aggs structure...")
resp = s3.list_objects_v2(Bucket=BUCKET, Prefix='us_stocks_sip/minute_aggs_v1/2026/01/', MaxKeys=30)

for obj in resp.get('Contents', []):
    key = obj['Key']
    size_mb = obj['Size'] / 1024 / 1024
    print(f"  {key} - {size_mb:.1f} MB")
