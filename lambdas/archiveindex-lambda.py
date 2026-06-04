import json
import boto3
from datetime import datetime

# AWS clients
s3_client = boto3.client('s3')

# Config
S3_BUCKET = 'sbf-newsletter'
METADATA_PREFIX = 'metadata/'

def lambda_handler(event, context):
    """
    Archive Index Lambda.
    Flow:
    1. List all objects in s3://sbf-newsletter/metadata/
    2. Fetch each JSON file
    3. Return sorted array of { month, title, theme, s3Key }
    """
    try:
        # List all objects in metadata/ folder
        response = s3_client.list_objects_v2(
            Bucket=S3_BUCKET,
            Prefix=METADATA_PREFIX
        )

        contents = response.get('Contents', [])

        if not contents:
            return success_response([])

        newsletters = []

        for obj in contents:
            key = obj['Key']

            # Skip the folder itself and non-JSON files
            if key == METADATA_PREFIX or not key.endswith('.json'):
                continue

            try:
                file_response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                metadata = json.loads(file_response['Body'].read().decode('utf-8'))

                newsletters.append({
                    'month': metadata.get('month', ''),
                    'title': metadata.get('title', ''),
                    'theme': metadata.get('theme', ''),
                    's3Key': metadata.get('s3Key', '')
                })

            except Exception as e:
                print(f"Error reading metadata file {key}: {str(e)}")
                continue

        # Sort newest first — "June 2026" format
        newsletters.sort(key=lambda x: parse_month(x['month']), reverse=True)

        return success_response(newsletters)

    except Exception as e:
        print(f"Error: {str(e)}")
        return error_response(f"Failed to fetch archive index: {str(e)}", 500)


def parse_month(month_str):
    """Parse 'June 2026' into a sortable datetime. Returns epoch 0 on failure."""
    try:
        return datetime.strptime(month_str, '%B %Y')
    except Exception:
        return datetime.min


def success_response(data):
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        'body': json.dumps({'success': True, 'data': data})
    }


def error_response(message, status_code=500):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({'success': False, 'error': message})
    }
