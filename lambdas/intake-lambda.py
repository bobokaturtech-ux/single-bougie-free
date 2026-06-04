import json
import boto3
import uuid
import re
import hmac
import hashlib
import base64
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
    Main handler for intake form submissions.
    Flow:
    1. Parse and validate form data
    2. Check if email already exists in DynamoDB
    3. Fetch current newsletter from S3
    4. Generate welcome email with newsletter embedded
    5. Send email via SES
    6. Store submission in DynamoDB
    7. Return success + user data
    """
    
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        
        # Extract and validate form fields
        email = body.get('email', '').strip()
        name = body.get('name', '').strip()
        age = body.get('age')
        zipcode = body.get('zipcode', '').strip()
        referral = body.get('referral', '').strip()
        designation = body.get('designation', '').strip()
        
        # Validation
        validation_error = validate_form(email, name, age, zipcode, referral, designation)
        if validation_error:
            return error_response(validation_error, 400)
        
        # Check if email already exists
        existing = check_email_exists(email)
        if existing:
            return error_response("This email is already subscribed to our newsletter", 409)
        
        # Fetch current newsletter from S3
        newsletter_html = get_current_newsletter_from_s3()
        if not newsletter_html:
            return error_response("Newsletter not available. Please try again later.", 500)
        
        # Generate submission ID
        submission_id = str(uuid.uuid4())
        submitted_at = datetime.utcnow().isoformat() + 'Z'
        
        # Generate welcome email
        email_subject, email_body = generate_welcome_email(name, email, newsletter_html)
        
        # Store submission in DynamoDB first — always save the record
        store_submission(submission_id, submitted_at, email, name, age, zipcode, referral, designation)
        
        # Send welcome email via SES — non-blocking, logs failure but does not stop success
        send_result = send_email_via_ses(email, email_subject, email_body)
        if not send_result:
            print(f"SES send failed for {email} — record saved, email pending production access")
        
        # Return success with user data
        return success_response({
            'submissionId': submission_id,
            'email': email,
            'name': name,
            'message': f'Welcome {name}! Check your email for your first newsletter.'
        })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return error_response("An unexpected error occurred. Please try again.", 500)


def validate_form(email, name, age, zipcode, referral, designation):
    """Validate all form fields with specific error messages."""
    
    # Name validation
    if not name or len(name) < 2:
        return "Please enter a valid name"
    
    # Email validation (strict RFC 5322)
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        return "Please enter a valid email address"
    
    # Age validation (18+, no max limit)
    try:
        age_int = int(age)
        if age_int < 18:
            return "You must be 18 or older to subscribe"
    except (ValueError, TypeError):
        return "Please enter a valid age"
    
    # Zipcode validation (US 5-digit only)
    if not re.match(r'^\d{5}$', zipcode):
        return "Please enter a valid US 5-digit zipcode"
    
    # Referral validation
    if not referral or referral == "":
        return "Please select how you heard about us"
    
    # Designation validation
    if not designation or len(designation) < 2:
        return "Please enter a valid job title or role"
    
    return None


def check_email_exists(email):
    """Check if email already exists in DynamoDB."""
    try:
        response = table.scan(
            FilterExpression='email = :email',
            ExpressionAttributeValues={':email': email}
        )
        return len(response.get('Items', [])) > 0
    except Exception as e:
        print(f"Error checking email: {str(e)}")
        return False


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


def generate_welcome_email(name, email, newsletter_html):
    """Generate personalized welcome email with newsletter embedded."""
    
    subject = f"Hi {name}, welcome to Spill The Tea"
    unsubscribe_link = get_unsubscribe_link(email)
    
    intro_text = f"""
    <p>Hey {name}! 👋</p>
    <p>Welcome to <strong>Spill The Tea</strong>. We're excited to have you here.</p>
    <p>Below is your first issue—packed with insights on legal updates, mental health, self-care, career moves, financial tips, and an exclusive spotlight interview.</p>
    <p>Enjoy, and we'll see you next month!</p>
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
    
    return subject, email_body


def send_email_via_ses(to_email, subject, html_body):
    """Send email via AWS SES."""
    try:
        ses_client.send_email(
            Source=SES_SENDER_EMAIL,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Html': {'Data': html_body}}
            }
        )
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False


def store_submission(submission_id, submitted_at, email, name, age, zipcode, referral, designation):
    """Store submission in DynamoDB."""
    try:
        table.put_item(
            Item={
                'submissionID': submission_id,
                'submittedAt': submitted_at,
                'email': email,
                'name': name,
                'age': age,
                'zipcode': zipcode,
                'referral': referral,
                'designation': designation,
                'optedIn': True,
                'emailsSentCount': 1,
                'source': 'web_form'
            }
        )
        print(f"Stored submission: {submission_id}")
    except Exception as e:
        print(f"Error storing submission: {str(e)}")
        raise


def success_response(data):
    """Return success response to frontend."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'success': True,
            'data': data
        })
    }


def error_response(message, status_code=400):
    """Return error response to frontend."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'success': False,
            'error': message
        })
    }