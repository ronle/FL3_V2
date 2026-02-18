"""
Polygon/Massive Flat Files - S3 Access

IMPORTANT: The S3 credentials are DIFFERENT from your API key!
You need to get S3 Access Key and Secret Key from:
https://polygon.io/dashboard (or massive.com/dashboard)

Look for "Flat Files" or "S3 Access" section.
"""

print("""
============================================================
POLYGON FLAT FILES - S3 ACCESS SETUP
============================================================

The 403 Forbidden error is because S3 access requires 
DIFFERENT credentials than the REST API key!

STEPS TO GET S3 CREDENTIALS:

1. Go to: https://polygon.io/dashboard
   (or https://massive.com/dashboard)

2. Look for "Flat Files" or "S3 Access" section

3. Generate/copy your:
   - S3 Access Key
   - S3 Secret Key
   
   These are DIFFERENT from your API key!

4. Then use in boto3:

   import boto3
   from botocore.config import Config
   
   session = boto3.Session(
       aws_access_key_id='YOUR_S3_ACCESS_KEY',
       aws_secret_access_key='YOUR_S3_SECRET_KEY',
   )
   
   s3 = session.client(
       's3',
       endpoint_url='https://files.polygon.io',
       config=Config(signature_version='s3v4'),
   )

============================================================
ALTERNATIVE: Use the Web File Browser
============================================================

You can also download files directly from the web browser:
https://polygon.io/flat-files/us_options_opra/trades_v1

Just sign in and click to download the files you need.

============================================================
WHAT'S AVAILABLE (from the file browser):
============================================================

us_options_opra/trades_v1/
  2026/  (18 files, 1.06 GB) - Current year
    01/
      2026-01-27.csv.gz  <- Yesterday's trades!
  2025/  (250 files, 13.5 GB)
  2024/  (252 files, 10.5 GB)
  2023/  (250 files, 9.04 GB)
  2022/  (254 files, 9.62 GB)
  2021/  (252 files, 10.3 GB)
  ... back to 2014

Each daily file contains ALL options trades market-wide!
============================================================
""")

# Try to check if user has separate S3 creds
import os

s3_access = os.environ.get('POLYGON_S3_ACCESS_KEY')
s3_secret = os.environ.get('POLYGON_S3_SECRET_KEY')

if s3_access and s3_secret:
    print("Found S3 credentials in environment, testing...")
    
    import boto3
    from botocore.config import Config
    
    session = boto3.Session(
        aws_access_key_id=s3_access,
        aws_secret_access_key=s3_secret,
    )
    
    # Try both endpoints (polygon and massive)
    for endpoint in ['https://files.polygon.io', 'https://files.massive.com']:
        print(f"\nTrying endpoint: {endpoint}")
        try:
            s3 = session.client(
                's3',
                endpoint_url=endpoint,
                config=Config(signature_version='s3v4'),
            )
            
            response = s3.list_objects_v2(
                Bucket='flatfiles',
                Prefix='us_options_opra/',
                MaxKeys=5
            )
            
            if 'Contents' in response:
                print(f"SUCCESS! Found {len(response['Contents'])} items")
                for obj in response['Contents']:
                    print(f"  {obj['Key']}")
                break
        except Exception as e:
            print(f"  Error: {e}")
else:
    print("""
No S3 credentials found in environment.

To set them (Windows PowerShell):
  $env:POLYGON_S3_ACCESS_KEY = "your-access-key"
  $env:POLYGON_S3_SECRET_KEY = "your-secret-key"
  
Then run this script again.

Or manually download from:
https://polygon.io/flat-files/us_options_opra/trades_v1/2026/01
""")
