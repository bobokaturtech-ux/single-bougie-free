import json
import boto3
import hmac
import hashlib
import base64
import time
from datetime import datetime
from botocore.exceptions import ClientError

# AWS clients
dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')

# Environment variables
DYNAMODB_TABLE = 'IntakeSubmissions'
SECRET_ARN = 'arn:aws:secretsmanager:us-east-1:013545664322:secret:sbf/newsletter/secret-Gh2vgm'
TOKEN_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 7 days

table = dynamodb.Table(DYNAMODB_TABLE)

# Cache secret key to avoid repeated Secrets Manager calls
_cached_secret = None

def lambda_handler(event, context):
    try:
        # Handle both HTTP API (v2) and REST API (v1) event formats
        query_params = (
            event.get('queryStringParameters') or
            event.get('rawQueryString') and dict(
                p.split('=') for p in event.get('rawQueryString', '').split('&') if '=' in p
            ) or {}
        )
        
        print(f"Event: {json.dumps(event)}")
        print(f"Query params: {query_params}")
        token = query_params.get('token', '').strip()
        step = query_params.get('step', '1')
        
        if not token:
            return error_page("Invalid or missing unsubscribe link. Please check your email.", 400)
        
        email = verify_token(token)
        if not email:
            return error_page("This unsubscribe link has expired or is invalid. Please request a new one.", 400)
        
        user = get_user_by_email(email)
        if not user:
            return error_page("User not found. You may already be unsubscribed.", 404)
        
        if step == '1':
            return confirmation_page(user, token)
        elif step == '2':
            success = unsubscribe_user(user['submissionID'], user['submittedAt'])
            if success:
                return success_page(user)
            else:
                return error_page("Failed to unsubscribe. Please try again.", 500)
        else:
            return error_page("Invalid request.", 400)
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return error_page("An unexpected error occurred. Please try again.", 500)


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


def verify_token(token):
    try:
        secret_key = get_secret_key()
        token_data = base64.urlsafe_b64decode(token.encode('utf-8')).decode('utf-8')
        parts = token_data.split(':')
        
        if len(parts) != 3:
            print("Invalid token format")
            return None
        
        email, expiry_str, signature = parts
        expiry = int(expiry_str)
        
        if time.time() > expiry:
            print(f"Token expired for {email}")
            return None
        
        message = f"{email}:{expiry}"
        expected_signature = hmac.new(
            secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            print(f"Invalid signature for {email}")
            return None
        
        print(f"Valid token for {email}")
        return email
    
    except Exception as e:
        print(f"Error verifying token: {str(e)}")
        return None


def get_user_by_email(email):
    try:
        response = table.scan(
            FilterExpression='email = :email',
            ExpressionAttributeValues={':email': email}
        )
        items = response.get('Items', [])
        return items[0] if items else None
    except Exception as e:
        print(f"Error querying user: {str(e)}")
        return None


def unsubscribe_user(submission_id, submitted_at):
    try:
        table.update_item(
            Key={'submissionID': submission_id, 'submittedAt': submitted_at},
            UpdateExpression='SET optedIn = :false, unsubscribedAt = :now',
            ExpressionAttributeValues={
                ':false': False,
                ':now': datetime.utcnow().isoformat() + 'Z'
            }
        )
        print(f"Unsubscribed user: {submission_id}")
        return True
    except Exception as e:
        print(f"Error unsubscribing user: {str(e)}")
        return False


def confirmation_page(user, token):
    name = user.get('name', 'Baddie')
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Unsubscribe - Spill The Tea</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #6B4C9A 0%, #E74C8C 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 16px;
                padding: 40px;
                max-width: 500px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }}
            h1 {{ color: #6B4C9A; font-size: 28px; margin-bottom: 20px; }}
            p {{ color: #666; font-size: 16px; line-height: 1.6; margin-bottom: 20px; }}
            .buttons {{ display: flex; gap: 15px; justify-content: center; flex-wrap: wrap; margin-top: 20px; }}
            .btn {{
                padding: 12px 30px;
                font-size: 14px;
                font-weight: bold;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                text-decoration: none;
                transition: all 0.3s ease;
            }}
            .btn-confirm {{ background: #E74C8C; color: white; }}
            .btn-confirm:hover {{ background: #d13878; transform: translateY(-2px); }}
            .btn-cancel {{ background: #ddd; color: #333; }}
            .btn-cancel:hover {{ background: #ccc; }}
            .footer {{ margin-top: 30px; color: #999; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>We'll Miss You, {name}! 💔</h1>
            <p>No hard feelings if you need to chill on the newsletter.</p>
            <div class="buttons">
                <a href="?token={token}&step=2" class="btn btn-confirm">
                    Yes, I'm Sure. No Hard Feelings
                </a>
                <a href="https://singlebougiefree.com/free.html" class="btn btn-cancel">Stay Subscribed</a>
            </div>
            <div class="footer">
                <p>If you believe this is a mistake, you can always re-subscribe on the website.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html; charset=utf-8'},
        'body': html
    }


def success_page(user):
    name = user.get('name', 'Baddie')
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Unsubscribed - Spill The Tea</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #6B4C9A 0%, #E74C8C 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 16px;
                padding: 40px;
                max-width: 500px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }}
            h1 {{ color: #6B4C9A; font-size: 28px; margin-bottom: 10px; }}
            .subtitle {{ color: #E74C8C; font-size: 18px; margin-bottom: 20px; }}
            p {{ color: #666; font-size: 16px; line-height: 1.6; margin-bottom: 20px; }}
            .success-icon {{ font-size: 48px; margin-bottom: 20px; }}
            .btn {{
                display: inline-block;
                padding: 12px 30px;
                font-size: 14px;
                font-weight: bold;
                background: #6B4C9A;
                color: white;
                border: none;
                border-radius: 8px;
                text-decoration: none;
                transition: all 0.3s ease;
            }}
            .btn:hover {{ background: #5a3d7f; transform: translateY(-2px); }}
            .footer {{ margin-top: 30px; color: #999; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">✌️</div>
            <h1>Message Received, {name}!</h1>
            <div class="subtitle">Go In Peace</div>
            <p>I totally understand. If you change your mind, you can always re-subscribe anytime.</p>
            <a href="https://singlebougiefree.com/free.html" class="btn">Back to Home</a>
            <div class="footer">
                <p>No emails will be sent to this address moving forward.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html; charset=utf-8'},
        'body': html
    }


def error_page(message, status_code=400):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Error - Spill The Tea</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #6B4C9A 0%, #E74C8C 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 16px;
                padding: 40px;
                max-width: 500px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            }}
            h1 {{ color: #E74C8C; font-size: 24px; margin-bottom: 20px; }}
            p {{ color: #666; font-size: 16px; line-height: 1.6; margin-bottom: 20px; }}
            .btn {{
                display: inline-block;
                padding: 12px 30px;
                font-size: 14px;
                font-weight: bold;
                background: #6B4C9A;
                color: white;
                border: none;
                border-radius: 8px;
                text-decoration: none;
            }}
            .btn:hover {{ background: #5a3d7f; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Oops! Something Went Wrong</h1>
            <p>{message}</p>
            <a href="https://singlebougiefree.com/free.html" class="btn">Back to Home</a>
        </div>
    </body>
    </html>
    """
    
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'text/html; charset=utf-8'},
        'body': html
    }