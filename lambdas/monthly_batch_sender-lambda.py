import json
import boto3
import re
import hmac
import hashlib
import base64
import time
from datetime import datetime
from botocore.exceptions import ClientError

# AWS clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
ses_client = boto3.client('ses')
secrets_client = boto3.client('secretsmanager')

# Environment variables
DYNAMODB_TABLE = 'IntakeSubmissions'
S3_BUCKET = 'sbf-newsletter'
SES_SENDER_EMAIL = 'newsletter@singlebougiefree.com'
SECRET_ARN = 'arn:aws:secretsmanager:us-east-1:013545664322:secret:sbf/newsletter/secret-Gh2vgm'
UNSUBSCRIBE_ENDPOINT = 'https://f2u235jmed.execute-api.us-east-1.amazonaws.com/default/sbf-unsubscribe-handler'
TOKEN_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 7 days

table = dynamodb.Table(DYNAMODB_TABLE)

# Cache secret key
_cached_secret = None

def get_secret_key():
    global _cached_secret
    if _cached_secret:
        return _cached_secret
    try:
        response = secrets_client.get_secret_value(SecretId=SECRET_ARN)
        secret = json.loads(response['SecretString'])
        _cached_secret = secret['SECRET_KEY']
        return _cached_secret
    except ClientError as e:
        print(f"Error fetching secret: {str(e)}")
        raise

def generate_unsubscribe_token(email):
    """Generate signed unsubscribe token valid for 7 days."""
    secret_key = get_secret_key()
    expiry = int(time.time()) + TOKEN_EXPIRY_SECONDS
    message = f"{email}:{expiry}"
    signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    token_data = f"{message}:{signature}"
    return base64.urlsafe_b64encode(token_data.encode('utf-8')).decode('utf-8')

def get_unsubscribe_link(email):
    """Generate full unsubscribe URL for a given email."""
    token = generate_unsubscribe_token(email)
    return f"{UNSUBSCRIBE_ENDPOINT}?token={token}&step=1"

def lambda_handler(event, context):
    """
    Batch Newsletter Sender Lambda.
    Triggered by EventBridge on 1st of month at 8 AM EST (1 PM UTC).
    
    Flow:
    1. Fetch current newsletter from S3 (most recent *-newsletter.html)
    2. Query DynamoDB for all users where optedIn = true
    3. For each user, check lastNewsletterReceived to avoid duplicates
    4. Send personalized newsletter via SES
    5. Update lastNewsletterReceived and emailsSentCount
    6. Log errors and retry failed sends once
    7. Return summary
    """
    
    try:
        # Get current newsletter from S3
        newsletter_html = get_current_newsletter_from_s3()
        if not newsletter_html:
            return error_response("No newsletter found in S3", 500)
        
        # Extract month from filename for subject line
        month = extract_month_from_newsletter(newsletter_html)
        
        # Get all opted-in users
        opted_in_users = get_opted_in_users()
        if not opted_in_users:
            return success_response({
                'sent_count': 0,
                'failed_count': 0,
                'message': 'No opted-in users found'
            })
        
        # Filter out users who already received this month's newsletter
        users_to_send = filter_users_by_last_received(opted_in_users, month)
        
        sent_count = 0
        failed_count = 0
        
        # Send newsletter to each user
        for user in users_to_send:
            success = send_newsletter_to_user(user, newsletter_html, month)
            
            if success:
                sent_count += 1
                # Update user record
                update_user_newsletter_received(user['submissionID'], user['submittedAt'], month)
            else:
                failed_count += 1
        
        return success_response({
            'sent_count': sent_count,
            'failed_count': failed_count,
            'total_users': len(opted_in_users),
            'filtered_users': len(users_to_send),
            'message': f'Sent {sent_count} newsletters, {failed_count} failed'
        })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return error_response(f"Batch send failed: {str(e)}", 500)


def get_current_newsletter_from_s3():
    """
    Scan S3 bucket for all *-newsletter.html files.
    Return the most recent one by LastModified timestamp.
    """
    try:
        response = s3_client.list_objects_v2(
            Bucket=S3_BUCKET,
            Prefix=''
        )
        
        files = response.get('Contents', [])
        if not files:
            print("No newsletter files found in S3")
            return None
        
        # Filter for newsletter HTML files only
        newsletter_files = [f for f in files if f['Key'].endswith('-newsletter.html')]
        
        if not newsletter_files:
            print("No newsletter HTML files found in S3")
            return None
        
        # Sort by LastModified (most recent first)
        sorted_files = sorted(newsletter_files, key=lambda x: x['LastModified'], reverse=True)
        latest_file = sorted_files[0]['Key']
        
        # Get file content
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=latest_file)
        newsletter_html = obj['Body'].read().decode('utf-8')
        
        print(f"Fetched newsletter: {latest_file}")
        return newsletter_html
    
    except Exception as e:
        print(f"Error fetching newsletter from S3: {str(e)}")
        return None


def extract_month_from_newsletter(html):
    """
    Extract month from newsletter HTML.
    Looks for <p> tag with month like "May 2026".
    Falls back to current month if not found.
    """
    try:
        # Look for pattern like "May 2026" in the HTML
        match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', html)
        if match:
            return f"{match.group(1)} {match.group(2)}"
        
        # Fallback: use current month
        now = datetime.utcnow()
        month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                       'July', 'August', 'September', 'October', 'November', 'December']
        return f"{month_names[now.month - 1]} {now.year}"
    
    except Exception as e:
        print(f"Error extracting month: {str(e)}")
        return "Current Month"


def get_opted_in_users():
    """
    Query DynamoDB for all users where optedIn = true.
    Uses scan with filter.
    """
    try:
        response = table.scan(
            FilterExpression='optedIn = :true',
            ExpressionAttributeValues={':true': True}
        )
        
        users = response.get('Items', [])
        print(f"Found {len(users)} opted-in users")
        
        # Handle pagination if needed
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                FilterExpression='optedIn = :true',
                ExpressionAttributeValues={':true': True},
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            users.extend(response.get('Items', []))
        
        return users
    
    except Exception as e:
        print(f"Error querying users: {str(e)}")
        return []


def filter_users_by_last_received(users, current_month):
    """
    Filter out users who already received this month's newsletter.
    Avoid duplicate sends.
    """
    filtered = []
    
    for user in users:
        last_received = user.get('lastNewsletterReceived', '')
        
        # If no record of last newsletter, send it
        if not last_received:
            filtered.append(user)
        else:
            # Check if current_month is NOT in lastNewsletterReceived
            # (e.g., if last_received = "2026-05-01T09:00:00Z" and current_month = "May 2026", skip)
            if current_month.lower().split()[0] not in last_received.lower():
                filtered.append(user)
    
    print(f"Filtered {len(filtered)} users (avoiding duplicates)")
    return filtered


def send_newsletter_to_user(user, newsletter_html, month):
    """
    Generate personalized newsletter email and send via SES.
    Retry once on failure.
    """
    name = user.get('name', 'Friend')
    email = user.get('email')
    
    # Generate email subject and body
    subject = f"Spilling the Tea: {month}"
    unsubscribe_link = get_unsubscribe_link(email)
    
    intro_text = f"""
    <p>Hi {name},</p>
    <p>Here's to another month of living our best life on our terms.</p>
    <p>Enjoy this month's newsletter and feel free to share it with any sister that needs it.</p>
    <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
    """
    
    email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        {intro_text}
        {newsletter_html}
        <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
        <p style="font-size: 12px; color: #999;">
            <em>Need to chill on the newsletter and unsubscribe Baddie? 
            <a href="{unsubscribe_link}" style="color: #E74C8C;">Click here.</a> No hard feelings.</em>
        </p>
    </body>
    </html>
    """
    
    # Try to send, retry once on failure
    max_retries = 1
    for attempt in range(max_retries + 1):
        try:
            ses_client.send_email(
                Source=SES_SENDER_EMAIL,
                Destination={'ToAddresses': [email]},
                Message={
                    'Subject': {'Data': subject},
                    'Body': {'Html': {'Data': email_body}}
                }
            )
            print(f"Email sent to {email} (attempt {attempt + 1})")
            return True
        
        except ClientError as e:
            error_code = e.response['Error']['Code']
            print(f"Error sending to {email} (attempt {attempt + 1}): {error_code}")
            
            if attempt < max_retries:
                print(f"Retrying {email}...")
                continue
            else:
                print(f"Failed to send to {email} after {max_retries + 1} attempts")
                return False
    
    return False


def update_user_newsletter_received(submission_id, submitted_at, month):
    """
    Update user record with:
    - lastNewsletterReceived: current timestamp
    - emailsSentCount: increment by 1
    """
    try:
        table.update_item(
            Key={'submissionID': submission_id, 'submittedAt': submitted_at},
            UpdateExpression='SET lastNewsletterReceived = :now, emailsSentCount = emailsSentCount + :inc',
            ExpressionAttributeValues={
                ':now': datetime.utcnow().isoformat() + 'Z',
                ':inc': 1
            }
        )
        print(f"Updated user {submission_id}")
    except Exception as e:
        print(f"Error updating user {submission_id}: {str(e)}")


def success_response(data):
    """Return success response."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json'
        },
        'body': json.dumps({
            'success': True,
            'data': data
        })
    }


def error_response(message, status_code=400):
    """Return error response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json'
        },
        'body': json.dumps({
            'success': False,
            'error': message
        })
    }