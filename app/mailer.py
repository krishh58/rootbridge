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


def send_match_email(to_email: str, sender_display: str, ancestor_name: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping match email')
        return False

    safe_sender = html_escape(sender_display.replace('\r', '').replace('\n', ''))
    safe_ancestor = html_escape(ancestor_name)
    subject = f'{safe_sender} wants to collaborate on {safe_ancestor} — RootBridge'
    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1a3d2b">You have a research match on RootBridge</h2>
  <p><strong>{safe_sender}</strong> is also researching
     <strong>{safe_ancestor}</strong> and sent you a message.</p>
  <a href="https://rootbridge.app/app"
     style="display:inline-block;padding:.75rem 2rem;background:#4a7c59;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    View Message
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    You can turn off match notifications in your account settings.
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
        logger.error('Failed to send match email to %s: %s', to_email, exc)
        return False


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping password reset email')
        return False

    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1e40af">Reset your RootBridge password</h2>
  <p>We received a request to reset the password for your account. Click the button below
     to choose a new password. This link expires in 1 hour.</p>
  <a href="{html_escape(reset_url)}"
     style="display:inline-block;padding:.75rem 2rem;background:#2563eb;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Reset Password
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    If you didn't request this, you can safely ignore this email. Your password won't change.
  </p>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Reset your RootBridge password'
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send password reset email to %s: %s', to_email, exc)
        return False


def send_2fa_email(to_email: str, code: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping 2FA email')
        return False

    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1e40af">Your RootBridge login code</h2>
  <p>Enter this code to complete your sign-in. It expires in 10 minutes.</p>
  <div style="font-size:2rem;font-weight:700;letter-spacing:.5rem;color:#1e293b;
              background:#f1f5f9;padding:1rem 2rem;border-radius:8px;display:inline-block;
              margin:1rem 0">{html_escape(code)}</div>
  <p style="color:#64748b;font-size:.85rem">
    If you didn't try to sign in, someone may have your password — consider changing it.
  </p>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'Your RootBridge login code: {code}'
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send 2FA email to %s: %s', to_email, exc)
        return False


def send_verification_email(to_email: str, verify_url: str) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping verification email')
        return False

    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto">
  <h2 style="color:#1e40af">Verify your RootBridge email</h2>
  <p>Thanks for joining RootBridge. Click below to verify your email address.
     This link expires in 24 hours.</p>
  <a href="{html_escape(verify_url)}"
     style="display:inline-block;padding:.75rem 2rem;background:#2563eb;
            color:#fff;border-radius:8px;text-decoration:none;font-weight:600">
    Verify Email
  </a>
  <p style="color:#64748b;font-size:.85rem;margin-top:1.5rem">
    If you didn't create a RootBridge account, you can ignore this email.
  </p>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Verify your RootBridge email address'
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send verification email to %s: %s', to_email, exc)
        return False


def send_rescan_email(to_email: str, findings: list) -> bool:
    """
    findings: list of {person_name: str, items: [{source, record_type, url, title?}]}
    """
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        logger.warning('GMAIL credentials not set — skipping rescan email')
        return False

    total_new = sum(len(f['items']) for f in findings)
    people_count = len(findings)

    rows_html = ''
    for f in findings:
        name = html_escape(f['person_name'])
        rows_html += f'<tr><td colspan="2" style="padding:10px 0 4px;font-weight:700;color:#1e293b;font-size:.9rem">{name}</td></tr>'
        for item in f['items']:
            source = html_escape(item.get('source', '').replace('_', ' ').title())
            rtype  = html_escape(item.get('record_type', '').replace('_', ' ').title())
            url    = item.get('url', '#')
            title  = html_escape(item.get('title', '') or rtype or source)
            safe_url = url if url.startswith('http') else '#'
            rows_html += f'''<tr>
              <td style="padding:3px 0;font-size:.85rem;color:#334155">{source}</td>
              <td style="padding:3px 0;font-size:.85rem">
                <a href="{html_escape(safe_url)}" style="color:#2563eb">{title[:80]}</a>
              </td>
            </tr>'''

    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#1e293b">
  <div style="background:#1e293b;padding:20px 24px;border-radius:8px 8px 0 0">
    <span style="color:#e2d9c4;font-size:1.2rem;font-weight:700">🌿 RootBridge</span>
    <span style="color:#94a3b8;font-size:.85rem;margin-left:12px">Monthly Research Update</span>
  </div>
  <div style="background:#faf7f2;padding:24px;border:1px solid #e2d8cc;border-top:none;border-radius:0 0 8px 8px">
    <h2 style="margin:0 0 8px;font-size:1.2rem">
      We found {total_new} new record{'s' if total_new != 1 else ''} across {people_count} ancestor{'s' if people_count != 1 else ''} in your tree
    </h2>
    <p style="color:#64748b;font-size:.875rem;margin:0 0 20px">
      RootBridge automatically re-scanned your family tree this month against updated archives.
      Here's what's new:
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      {rows_html}
    </table>
    <a href="https://rootbridge.app/app"
       style="display:inline-block;padding:.7rem 2rem;background:#2563eb;color:#fff;
              border-radius:6px;text-decoration:none;font-weight:600;font-size:.9rem">
      View in RootBridge →
    </a>
    <p style="color:#94a3b8;font-size:.78rem;margin-top:20px">
      You're receiving this because you have a RootBridge account.
      Manage notification preferences in <a href="https://rootbridge.app/account" style="color:#2563eb">account settings</a>.
    </p>
  </div>
</div>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'RootBridge found {total_new} new record{"s" if total_new != 1 else ""} in your family tree'
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, to_email, msg.as_string())
        return True
    except Exception as exc:
        logger.error('Failed to send rescan email to %s: %s', to_email, exc)
        return False
