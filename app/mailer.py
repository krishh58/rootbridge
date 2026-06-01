import os
import smtplib
import logging
from html import escape as html_escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def send_invite_email(to_email: str, inviter_name: str, tree_name: str,
                      role: str, invite_url: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping invite email')
        return False

    safe_name = html_escape(inviter_name.replace('\r', '').replace('\n', ''))
    safe_tree = html_escape(tree_name)
    safe_role = html_escape(role)
    subject = f'{safe_name} invited you to collaborate on RootBridge'
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1e40af">You have a RootBridge invitation</h2>
  <p><strong>{safe_name}</strong> has invited you to collaborate on
     <strong>{safe_tree}</strong> as a <strong>{safe_role}</strong>.</p>
  <a href="https://rootbridge.app{invite_url}"
     style="display:inline-block;padding:.75rem 2rem;background:#2563eb;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Accept Invitation
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    This link expires in 7 days. If you weren't expecting this, you can ignore it.
  </p>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send invite email to %s: %s', to_email, exc)
        return False
