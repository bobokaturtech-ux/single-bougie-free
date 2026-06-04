import json
import boto3
import urllib.request
import urllib.parse
from datetime import datetime
import re

# AWS clients
s3_client = boto3.client('s3')
sns_client = boto3.client('sns')
secrets_client = boto3.client('secretsmanager')

# Config
AIRTABLE_BASE_ID = 'appCm21OdoSX0Y1iU'
AIRTABLE_TABLE_ID = 'tblPZze8z1oRGl97y'
S3_BUCKET = 'sbf-newsletter'
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:013545664322:sbf-newsletter-errors:17783835-596a-4e94-89bb-d880ea72e3f5'
SECRET_NAME = 'sbf/airtable_token'

# Cache token
_cached_token = None

def get_airtable_token():
    global _cached_token
    if _cached_token:
        return _cached_token
    response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
    secret = json.loads(response['SecretString'])
    _cached_token = secret['airtable_token']
    return _cached_token

def lambda_handler(event, context):
    """
    Newsletter Generator Lambda.
    Flow:
    1. Fetch all newsletters from Airtable where Status = "Ready"
    2. For each newsletter, generate styled HTML
    3. Upload to S3 with auto-generated filename (newsletters/ folder)
    4. Generate metadata JSON and upload to S3 (metadata/ folder)
    5. If metadata fails: log warning + send SNS alert, but newsletter still uploads
    6. Return success with generated filenames
    """
    
    try:
        # Fetch newsletters from Airtable
        newsletters = fetch_newsletters_from_airtable()
        
        if not newsletters:
            return error_response("No newsletters found with 'Ready' status", 400)
        
        generated_files = []
        
        for newsletter in newsletters:
            # Generate HTML
            html_content = generate_newsletter_html(newsletter)
            
            # Generate filename from Month field
            month = newsletter['fields']['Month']
            filename = generate_filename(month)
            newsletter_s3_key = f"newsletters/{filename}"
            
            # Upload newsletter HTML to S3
            upload_result = upload_to_s3(newsletter_s3_key, html_content)
            
            if upload_result:
                # Extract titles for metadata
                titles = extract_titles(newsletter['fields'])
                
                # Generate and upload metadata JSON
                metadata_key = f"metadata/{filename.replace('.html', '.json')}"
                metadata = {
                    'month': month,
                    'title': newsletter['fields'].get('Newsletter_Title', ''),
                    'theme': newsletter['fields'].get('Newsletter_Theme', ''),
                    'titles': titles,
                    's3Key': newsletter_s3_key
                }
                
                metadata_success = upload_metadata_to_s3(metadata_key, metadata)
                
                if not metadata_success:
                    send_sns_alert(
                        subject=f"Archive Metadata Error - {month}",
                        message=f"Failed to write metadata JSON for {month} newsletter. Newsletter was uploaded successfully, but it won't appear in the archive. File: {metadata_key}"
                    )
                    print(f"WARNING: Metadata write failed for {month}, but newsletter uploaded successfully")
                
                generated_files.append({
                    'month': month,
                    'filename': filename,
                    's3_url': f"https://{S3_BUCKET}.s3.amazonaws.com/{newsletter_s3_key}",
                    'metadata_success': metadata_success
                })

                # Update Airtable status to Published
                update_airtable_status(newsletter['id'], 'Published')
        
        return success_response({
            'generated_count': len(generated_files),
            'files': generated_files,
            'message': f"Successfully generated {len(generated_files)} newsletter(s)"
        })
    
    except Exception as e:
        print(f"Error: {str(e)}")
        return error_response(f"Failed to generate newsletter: {str(e)}", 500)


def fetch_newsletters_from_airtable():
    """Fetch all records from Airtable where Status = Ready."""
    try:
        token = get_airtable_token()
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
        
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        records = data.get('records', [])
        print(f"Fetched {len(records)} newsletter(s) from Airtable")
        
        for r in records:
            print(f"Record fields: {json.dumps(r.get('fields', {}))}")
        
        ready_records = [r for r in records if r.get('fields', {}).get('Status', '').strip() == 'Ready']
        print(f"Found {len(ready_records)} Ready newsletter(s)")
        
        return ready_records
    
    except Exception as e:
        print(f"Error fetching from Airtable: {str(e)}")
        return []


def extract_titles(fields):
    """Extract the 6 section titles from newsletter fields."""
    titles = [
        fields.get('Legal_Title', ''),
        fields.get('MentalHealth_Title', ''),
        fields.get('SelfCare_Title', ''),
        fields.get('Career_Title', ''),
        fields.get('Financial_Title', ''),
        fields.get('Spotlight_Title', '')
    ]
    return titles


def generate_newsletter_html(newsletter):
    """Generate branded dark purple HTML newsletter from Airtable record."""
    
    fields = newsletter['fields']
    month = fields.get('Month', 'Month Unknown')
    title = fields.get('Newsletter_Title', 'SBF Newsletter')
    theme = fields.get('Newsletter_Theme', '')
    issue = fields.get('Issue_Number', '')
    intro = fields.get('Intro_Message', 'Hey SBF Baddie — this month\'s issue is for you.')

    def card(category, card_title, blurb, link):
        # Ensure link has https:// prefix
        safe_link = link.strip()
        if safe_link and not safe_link.startswith('http'):
            safe_link = 'https://' + safe_link
        return f"""
        <tr>
          <td style="background-color:#2D0A2E; padding: 8px 30px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
              style="background-color:#EDE0F0; border-radius:12px; overflow:hidden;">
              <tr>
                <td style="padding: 18px 20px;">
                  <p style="margin:0 0 4px; font-family:Arial,sans-serif; font-size:10px;
                    font-weight:bold; letter-spacing:0.08em; text-transform:uppercase; color:#888888;">
                    {category}
                  </p>
                  <h3 style="margin:0 0 8px; font-family:Georgia,serif; font-size:16px;
                    font-weight:normal; color:#2D0A2E;">{card_title}</h3>
                  <p style="margin:0 0 12px; font-family:Arial,sans-serif; font-size:16px;
                    color:#555555; line-height:1.6;">{blurb}</p>
                  <a href="{safe_link}" target="_blank" rel="noopener noreferrer" style="font-family:Arial,sans-serif; font-size:12px;
                    font-weight:bold; color:#993556;">{safe_link} &rarr;</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    def section_header(label):
        return f"""
        <tr>
          <td style="background-color:#2D0A2E; padding: 20px 30px 8px;">
            <span style="display:inline-block; background-color:#7B2D8B; color:#F4C0D1;
              font-family:Georgia,serif; font-size:18px; padding:5px 18px; border-radius:20px;">
              {label}
            </span>
          </td>
        </tr>"""

    spotlight_quote = fields.get('SpotLight_Quote', 'Coming Next Month — Know a woman building on solid ground? We want to spotlight her.')
    spotlight_bio = fields.get('SpotLight_Bio', '')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta http-equiv="X-UA-Compatible" content="IE=edge"/>
  <title>Spilling the Tea: {month} Issue {issue}</title>
  <style>
    body, table, td, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
    table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; }}
    body {{ margin:0; padding:0; background-color:#1a001a; }}
    a {{ color:#D4537E; text-decoration:none; }}
    @media only screen and (max-width:600px) {{
      .email-container {{ width:100% !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:#1a001a;">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">
    Spilling the Tea: {title}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
    style="background-color:#1a001a;">
    <tr>
      <td align="center" style="padding:20px 10px;">
        <table class="email-container" role="presentation" width="600" cellpadding="0"
          cellspacing="0" border="0" style="max-width:600px; width:100%; background-color:#1a001a;">

          <!-- HERO -->
          <tr>
            <td align="center" style="background-color:#2D0A2E; padding:36px 30px 28px;
              border-radius:16px 16px 0 0;">
              <p style="margin:0 0 12px; font-family:Arial,sans-serif;">
                <span style="display:inline-block; background-color:#7B2D8B; color:#F4C0D1;
                  font-size:11px; letter-spacing:0.08em; padding:4px 14px; border-radius:20px;">
                  Spilling the Tea &nbsp;·&nbsp; Issue {issue} &nbsp;·&nbsp; {month}
                </span>
              </p>
              <h1 style="margin:0 0 8px; font-family:Georgia,serif; font-size:36px;
                font-weight:normal; color:#F4C0D1; line-height:1.2;">{title}</h1>
              <p style="margin:0; font-family:Arial,sans-serif; font-size:16px; color:#ED93B1;">
                {theme}
              </p>
            </td>
          </tr>

          <!-- GRADIENT DIVIDER -->
          <tr>
            <td style="height:4px; background:linear-gradient(90deg,#7B2D8B,#D4537E,#ED93B1);"></td>
          </tr>

          <!-- FROM KATURAH -->
          <tr>
            <td style="background-color:#F4C0D1; padding:24px 30px; border-bottom:1px solid #f0d0e0;">
              <p style="margin:0 0 8px; font-family:Arial,sans-serif; font-size:10px;
                font-weight:bold; letter-spacing:0.08em; text-transform:uppercase; color:#993556;">
                From Katurah
              </p>
              <p style="margin:0; font-family:Arial,sans-serif; font-size:17px;
                color:#444444; line-height:1.8;">{intro}</p>
            </td>
          </tr>

          <!-- SINGLE SECTION -->
          {section_header('Single')}
          {card('Legal', fields.get('Legal_Title',''), fields.get('Legal_Blurb',''), fields.get('Legal_Link',''))}
          {card('Financial', fields.get('Financial_Title',''), fields.get('Financial_Blurb',''), fields.get('Financial_Link',''))}
          {card('Career', fields.get('Career_Title',''), fields.get('Career_Blurb',''), fields.get('Career_Link',''))}

          <!-- BOUGIE SECTION -->
          {section_header('Bougie')}
          {card('Self Care', fields.get('SelfCare_Title',''), fields.get('SelfCare_Blurb',''), fields.get('SelfCare_Link',''))}
          {card('Mental Health', fields.get('MentalHealth_Title',''), fields.get('MentalHealth_Blurb',''), fields.get('MentalHealth_Link',''))}

          <!-- FREE SECTION -->
          {section_header('Free')}
          <tr>
            <td style="background-color:#2D0A2E; padding:8px 30px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                style="background-color:#7B2D8B; border-radius:12px;">
                <tr>
                  <td align="center" style="padding:24px 20px;">
                    <p style="margin:0; font-family:Georgia,serif; font-size:14px;
                      font-style:italic; color:#F4C0D1; line-height:1.8;">{spotlight_quote}</p>
                    <p style="margin:8px 0 0; font-family:Arial,sans-serif; font-size:12px;
                      color:#ED93B1; line-height:1.6;"><em>{spotlight_bio}</em></p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td align="center" style="background-color:#2D0A2E; padding:20px 30px;
              border-top:1px solid #7B2D8B; border-radius:0 0 16px 16px;">
              <p style="margin:0 0 6px; font-family:Georgia,serif; font-size:16px; color:#ED93B1;">
                Single. Bougie. Free.
              </p>
              <p style="margin:0; font-family:Arial,sans-serif; font-size:11px; color:#7B2D8B;">
                <a href="https://singlebougiefree.com" style="color:#ED93B1;">singlebougiefree.com</a>
                &nbsp;·&nbsp;
                <a href="{{unsubscribe_url}}" style="color:#7B2D8B;">Unsubscribe</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    return html


def generate_filename(month):
    """Input: 'June 2026' → Output: 'june2026-newsletter.html'"""
    try:
        filename = month.lower().replace(' ', '') + '-newsletter.html'
        return filename
    except Exception as e:
        print(f"Error generating filename: {str(e)}")
        return f"newsletter-{datetime.utcnow().timestamp()}.html"


def upload_to_s3(s3_key, html_content):
    """Upload HTML file to S3."""
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=html_content.encode('utf-8'),
            ContentType='text/html',
            Metadata={
                'Generated': datetime.utcnow().isoformat(),
                'Newsletter': 'true'
            }
        )
        print(f"Uploaded {s3_key} to S3")
        return True
    except Exception as e:
        print(f"Error uploading newsletter to S3: {str(e)}")
        return False


def upload_metadata_to_s3(metadata_key, metadata_dict):
    """Upload metadata JSON file to S3."""
    try:
        metadata_json = json.dumps(metadata_dict, indent=2)
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=metadata_key,
            Body=metadata_json.encode('utf-8'),
            ContentType='application/json',
            Metadata={
                'Generated': datetime.utcnow().isoformat(),
                'Type': 'newsletter-metadata'
            }
        )
        print(f"Uploaded {metadata_key} to S3")
        return True
    except Exception as e:
        print(f"Error uploading metadata to S3: {str(e)}")
        return False


def send_sns_alert(subject, message):
    """Send SNS notification for metadata write failures."""
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        print(f"SNS alert sent: {subject}")
        return True
    except Exception as e:
        print(f"Error sending SNS alert: {str(e)}")
        return False


def update_airtable_status(record_id, status):
    """Update the Status field of an Airtable record."""
    try:
        token = get_airtable_token()
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}"
        
        payload = json.dumps({
            "fields": {
                "Status": status
            }
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=payload, method='PATCH')
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req) as response:
            print(f"Airtable status updated to '{status}' for record {record_id}")
            return True
    except Exception as e:
        print(f"Warning: Failed to update Airtable status for {record_id}: {str(e)}")
        return False


def success_response(data):
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps({'success': True, 'data': data})
    }


def error_response(message, status_code=400):
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps({'success': False, 'error': message})
    }
