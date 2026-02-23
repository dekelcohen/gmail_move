# python -m src.gmail_forward_email_query

import os.path
import base64

# Mime email api
import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.message import MIMEMessage

# Google OAuth and Gmail APIs 
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# SCOPES with full mailbox access (modify, send, delete)
SCOPES = ['https://mail.google.com/']

# Change these values:
THRESHOLD_MB = 1  # Only messages over 10 MB
FORWARD_TO_EMAIL = "dekelcohen33@gmail.com"

def authenticate_gmail():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    service = build('gmail', 'v1', credentials=creds)
    return service

def get_all_message_ids(service):
    results = service.users().messages().list(userId='me', labelIds=['INBOX']).execute()
    return results.get('messages', [])

def get_message_ids_query(service, q=None):
    all_ids = []
    next_page_token = None

    print(f'get_message_ids_query q={q}.  may take a minute ...')
    while True:
        response = service.users().messages().list(
            userId='me',
            labelIds=['INBOX'],
            q=q,  # Search filter used here
            pageToken=next_page_token
        ).execute()

        if 'messages' in response:
            all_ids.extend(response['messages'])

        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break

    return all_ids
    
def fetch_message(service, msg_id):
    return service.users().messages().get(userId='me', id=msg_id, format='full').execute()


import base64
import email
import html
import re

import base64
import email
from email.header import decode_header
import html
import re

def decode_email_header(value):
    """
    Decodes RFC 2047 encoded email headers (e.g. =?utf-8?b?...?=) 
    into human-readable Unicode strings.
    """
    if not value:
        return ""
    
    decoded_fragments = []
    for fragment, encoding in decode_header(value):
        if isinstance(fragment, bytes):
            charset = encoding or 'utf-8'
            try:
                decoded_fragments.append(fragment.decode(charset, errors='replace'))
            except LookupError:
                decoded_fragments.append(fragment.decode('utf-8', errors='replace'))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments)


def forward_message(service, msg_id, forward_to, add_fwd_text: bool = False):
    """
    Forward a Gmail message preserving attachments and HTML perfectly,
    and prepend specifically requested decoded original headers to the email body.
    """
    # 1) Fetch raw original message (includes attachments)
    raw_msg = service.users().messages().get(
        userId="me", id=msg_id, format="raw"
    ).execute()

    # Decode the raw message content into a mutable Message object
    original_bytes = base64.urlsafe_b64decode(raw_msg["raw"])
    original_msg = email.message_from_bytes(original_bytes)

    # Extract useful headers for the new Subject / Reply-To
    orig_subject_raw = original_msg.get("Subject", "")
    orig_sender_raw = original_msg.get("From", "Unknown Sender")
    
    orig_subject = decode_email_header(orig_subject_raw)
    orig_sender = decode_email_header(orig_sender_raw)

    print(f"{orig_subject} (From: {orig_sender})")
    # ---------------------------------------------------------
    # HEADERS CONFIGURATION
    # ---------------------------------------------------------
    # These headers MUST be deleted so Gmail accepts the forward
    headers_to_remove = [
        'To', 'From', 'Cc', 'Bcc', 'Subject', 'Reply-To', 'Return-Path',
        'Delivered-To', 'Message-ID', 'Date', 'Received', 
        'DKIM-Signature', 'Authentication-Results',
        'ARC-Seal', 'ARC-Message-Signature', 'ARC-Authentication-Results'
    ]

    # These are the ONLY headers we will save and inject into the body
    headers_to_save = [
        'From', 'To', 'Cc', 'Subject', 'Date', 
        'Message-ID', 'Delivered-To', 'Return-Path'
    ]

    # 2) Collect and decode the important headers we want to keep visible
    saved_headers = []
    for header in headers_to_save:
        values = original_msg.get_all(header)
        if values:
            for v in values:
                # Decode values so things like =?utf-8?... become readable text
                decoded_v = decode_email_header(v)
                # Clean up excess whitespace/newlines (useful for multi-line Received headers)
                clean_v = re.sub(r'\s+', ' ', decoded_v).strip()
                if clean_v:
                    saved_headers.append((header, clean_v))

    # Build the plain text header block
    deleted_headers_text = "--- Original Important Headers ---\n"
    
    # Build the HTML header block
    deleted_headers_html = (
        "<div style='background-color: #f4f6f8; padding: 15px; margin-bottom: 20px; "
        "border-left: 4px solid #d0d7de; font-family: monospace; font-size: 13px; color: #24292f;'>"
        "<b style='color: #0969da;'>--- Original Important Headers ---</b><br><br>"
    )

    for header, value in saved_headers:
        # Plain text formatting
        deleted_headers_text += f"{header}: {value}\n"
        
        # HTML formatting (escape HTML chars)
        escaped_val = html.escape(value)
        deleted_headers_html += f"<b>{header}:</b> {escaped_val}<br>"

    deleted_headers_text += "----------------------------------\n\n"
    deleted_headers_html += "</div>"

    # 3) Actually strip all routing and signature headers from the email
    for header in headers_to_remove:
        del original_msg[header]

    # 4) Traverse the email body and inject the headers
    injected_plain = False
    injected_html = False

    for part in original_msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
            
        cdisp = str(part.get('Content-Disposition', '')).lower()
        if 'attachment' in cdisp:
            continue

        ctype = part.get_content_type()
        if ctype in ['text/plain', 'text/html']:
            charset = part.get_content_charset() or 'utf-8'
            try:
                payload_bytes = part.get_payload(decode=True)
                payload_str = payload_bytes.decode(charset, errors='replace') if payload_bytes else ""
            except Exception:
                payload_str = ""

            if ctype == 'text/plain' and not injected_plain:
                new_payload = deleted_headers_text + payload_str
                del part['Content-Transfer-Encoding']
                part.set_payload(new_payload)
                part.set_charset('utf-8')
                injected_plain = True

            elif ctype == 'text/html' and not injected_html:
                body_match = re.search(r'<body[^>]*>', payload_str, re.IGNORECASE)
                if body_match:
                    insert_pos = body_match.end()
                    new_payload = payload_str[:insert_pos] + deleted_headers_html + payload_str[insert_pos:]
                else:
                    new_payload = deleted_headers_html + payload_str
                
                del part['Content-Transfer-Encoding']
                part.set_payload(new_payload)
                part.set_charset('utf-8')
                injected_html = True

    # 5) Apply new forwarding headers
    original_msg["To"] = forward_to
    
    if add_fwd_text:
        original_msg["Subject"] = f"Fwd: {orig_subject} (From: {orig_sender})"
    else:
        original_msg["Subject"] = orig_subject
        
    original_msg["Reply-To"] = orig_sender

    # 6) Encode the perfectly preserved original message back to base64
    raw_forward = base64.urlsafe_b64encode(original_msg.as_bytes()).decode()

    # 7) Send via Gmail API
    sent_message = service.users().messages().send(
        userId="me",
        body={"raw": raw_forward}
    ).execute()
    
    return sent_message.get("id")

def trash_message(service, msg_id):
    service.users().messages().trash(userId='me', id=msg_id).execute()

def delete_message(service, msg_id):
    """
    Permanently deletes a message by ID, bypassing the Trash, 
    so that storage space is freed up immediately.
    """
    try:
        service.users().messages().delete(userId='me', id=msg_id).execute()
    except Exception as e:
        print(f"Failed to delete message {msg_id}: {e}")

def flush_sent_messages(service, sent_ids):
    """
    Attempts to delete sent messages. 
    Returns a list of IDs that failed to delete, so they can be retried later.
    """
    if not sent_ids:
        return []
        
    print(f"\n--- Attempting to delete {len(sent_ids)} 'Sent' messages in batch ---")
    failed_ids = []
    
    for sent_id in sent_ids:
        try:
            # We call the API directly here so we can catch the exact exception
            service.users().messages().delete(userId='me', id=sent_id).execute()
            print(f"  -> Successfully deleted Sent message: {sent_id}")
        except Exception as e:
            print(f"  -> Failed to delete Sent message {sent_id} (Will retry next batch). Reason: {e}")
            failed_ids.append(sent_id)
            
    return failed_ids
    
def main():
    service = authenticate_gmail()
    
    BATCH_SIZE = 4   
    threshold_bytes = THRESHOLD_MB * 1024 * 1024
    message_ids = get_message_ids_query(service, q="in:inbox before:2024/01/01 larger:1m") # has:attachment

    breakpoint()
    
    print(f'iterate {len(message_ids)} message_ids, fetch each and check size. if > {THRESHOLD_MB} MB --> forward it to {FORWARD_TO_EMAIL} and delete it from gmail account')
    
    sent_msg_ids_to_delete = []

    for i, m in enumerate(message_ids):
        # --- BATCH DELETION TRIGGER ---
        if i > 0 and i % BATCH_SIZE == 0:
            print(f'\n*** Processed {i} / {len(message_ids)} emails. Running batch deletion...')
            
            # Flush the sent messages collected so far.
            # It returns the ones that failed, updating the list so they are preserved for the next batch.
            sent_msg_ids_to_delete = flush_sent_messages(service, sent_msg_ids_to_delete)
            
            breakpoint() # (Uncomment if you still want to pause here)

        msg_id = m['id']
        try:
            try:
                msg = fetch_message(service, msg_id)
            except HttpError:
                continue
        
            size = msg.get('sizeEstimate', 0)
            if size > threshold_bytes:
                print(f'\nForward {msg_id} of size {round(size / 1024 / 1024, 2)} MB to {FORWARD_TO_EMAIL}')
                
                # Forward the message and capture the new Sent ID
                sent_msg_id = forward_message(service, msg_id, FORWARD_TO_EMAIL)
                
                if sent_msg_id:
                    # Save the ID to be deleted in the next batch
                    sent_msg_ids_to_delete.append(sent_msg_id)
                
                print(f'Trash {msg_id} from gmail account')
                trash_message(service, msg_id)                
        except Exception as e:
            print(f"Failed to process message {msg_id}: {e}")

    # --- FINAL CLEANUP (The Leftovers) ---
    if sent_msg_ids_to_delete:
        print(f"\n*** Finished iterating. Processing final {len(sent_msg_ids_to_delete)} leftover 'Sent' messages.")
        
        # Try one last time to delete the leftovers
        leftovers = flush_sent_messages(service, sent_msg_ids_to_delete)
        
        # If any STILL fail here at the very end, print a warning so you are aware
        if leftovers:
            print(f"\nWarning: {len(leftovers)} Sent messages could not be deleted automatically:")
            for leftover_id in leftovers:
                print(f" - {leftover_id}")
    else:
        print("\nFinished! No leftover 'Sent' messages to clean up.")

if __name__ == "__main__":
    main()
