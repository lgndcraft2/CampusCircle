"""Transactional email via Brevo (POST https://api.brevo.com/v3/smtp/email, header `api-key`).

Codes and links are rendered into the message only; they are never logged or stored in delivery payloads.
A `provider_accepted` status means Brevo returned a messageId, not that the message reached an inbox.
"""
import html
import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROOT, settings
from .models import EmailDelivery, User, now, uid

log = logging.getLogger('campuscircle.email')
BREVO_URL = 'https://api.brevo.com/v3/smtp/email'
MAX_ATTEMPTS = 3
transport: httpx.BaseTransport | None = None  # Tests inject httpx.MockTransport; never set in production.


class EmailUnavailable(Exception):
    pass


@dataclass
class Rendered:
    subject: str
    text: str
    html: str


# kind -> (subject, heading, plain body). Bodies must not carry passwords, secrets, BVNs, or balances.
TEMPLATES = {
    'verify': ('Verify your CampusCircle email', 'Verify your student email', 'Your verification code is {code}. It expires in {minutes} minutes and can be used once.'),
    'reset': ('Reset your CampusCircle password', 'Reset your password', 'Your password reset code is {code}. It expires in {minutes} minutes. If you did not ask for this, you can ignore this email; your password is unchanged.'),
    'circle': ('{title}', '{title}', '{body}'),
    'finance': ('{title}', '{title}', '{body}'),
    'security': ('Security notice: {title}', '{title}', '{body}'),
}


def render(kind: str, name: str, **values) -> Rendered:
    subject, heading, body = (part.format(**values) for part in TEMPLATES[kind])
    link = f"{settings().frontend_url}{values.get('link', '/app')}"
    text = f'Hi {name},\n\n{body}\n\nOpen CampusCircle: {link}\n\nCampusCircle · Save together. Decide together. Grow together.\nThis is an automated message about your account.'
    e = html.escape
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(subject)}</title></head>
<body style="margin:0;background:#f4f2fd;font-family:Arial,Helvetica,sans-serif;color:#1d1b3a">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:16px;border:1px solid #e4e0f7">
<tr><td style="padding:24px 28px 0;font-size:18px;font-weight:bold;color:#4f3fc4">CampusCircle</td></tr>
<tr><td style="padding:12px 28px 0"><h1 style="font-size:22px;line-height:1.3;margin:0 0 12px">{e(heading)}</h1>
<p style="font-size:15px;line-height:1.6;margin:0 0 12px">Hi {e(name)},</p>
<p style="font-size:15px;line-height:1.6;margin:0 0 20px">{e(body)}</p>
<p style="margin:0 0 24px"><a href="{e(link)}" style="display:inline-block;background:#5b4bd6;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:10px;font-weight:bold">Open CampusCircle</a></p></td></tr>
<tr><td style="padding:16px 28px 24px;border-top:1px solid #eeebfa;font-size:12px;line-height:1.5;color:#5d5a7a">Save together. Decide together. Grow together.<br>This is an automated message about your CampusCircle account. Financial activity in this demo is simulated or sandbox-only.</td></tr>
</table></td></tr></table></body></html>"""
    return Rendered(subject, text, page)


def _deliver(user: User, message: Rendered) -> tuple[str, str | None]:
    """Returns (provider, message_id). Raises EmailUnavailable on definite failure."""
    cfg = settings()
    if cfg.email_provider == 'brevo':
        try:
            with httpx.Client(timeout=15, transport=transport) as client:
                response = client.post(BREVO_URL, headers={'api-key': cfg.brevo_api_key, 'accept': 'application/json'}, json={
                    'sender': {'name': cfg.brevo_sender_name, 'email': cfg.brevo_sender_email},
                    'to': [{'email': user.email, 'name': user.full_name}],
                    'subject': message.subject, 'htmlContent': message.html, 'textContent': message.text})
        except httpx.HTTPError as exc:
            raise EmailUnavailable(f'network: {type(exc).__name__}') from exc
        if response.status_code >= 300:
            raise EmailUnavailable(f'brevo status {response.status_code}')
        try:
            message_id = response.json().get('messageId')
        except ValueError:
            message_id = None
        if not message_id:
            raise EmailUnavailable('brevo response missing messageId')
        return 'brevo', str(message_id)
    if cfg.email_provider == 'development_outbox':
        outbox = ROOT / '.dev-mail'
        outbox.mkdir(exist_ok=True)
        # Private developer filesystem only; never served over HTTP.
        (outbox / f'{now().strftime("%Y%m%d%H%M%S")}-{uid()}.json').write_text(
            json.dumps({'to': user.email, 'subject': message.subject, 'body': message.text}), encoding='utf-8')
        return 'development_outbox', None
    raise EmailUnavailable('no email provider configured')


def send_now(db: Session, user: User, kind: str, dedupe_key: str, **values) -> EmailDelivery:
    """Synchronous send for critical messages (verification, reset). Raises EmailUnavailable."""
    message = render(kind, user.full_name, **values)
    row = EmailDelivery(user_id=user.id, kind=kind, dedupe_key=dedupe_key, subject=message.subject, attempts=1)
    db.add(row)
    try:
        row.provider, row.provider_message_id = _deliver(user, message)
        row.status = 'provider_accepted' if row.provider == 'brevo' else 'written_to_outbox'
    except EmailUnavailable as exc:
        row.status, row.last_error = 'failed', str(exc)[:300]
        db.flush()
        log.warning('email.failed', extra={'kind': kind, 'delivery_id': row.id, 'reason': row.last_error})
        raise
    db.flush()
    return row


def enqueue(db: Session, user: User, kind: str, dedupe_key: str, title: str, body: str, link: str):
    """Queue a noncritical notification email. Duplicate keys are ignored, so retries never double-send."""
    if settings().email_provider == 'unavailable' or not user.notification_preferences.get('email', True):
        return
    if db.scalar(select(EmailDelivery.id).where(EmailDelivery.dedupe_key == dedupe_key)):
        return
    subject = render(kind, user.full_name, title=title, body=body, link=link).subject
    db.add(EmailDelivery(user_id=user.id, kind=kind, dedupe_key=dedupe_key, subject=subject, payload={'title': title, 'body': body, 'link': link}))


def process_queue(db: Session, limit: int = 25) -> int:
    rows = db.scalars(select(EmailDelivery).where(EmailDelivery.status.in_(['queued', 'retrying'])).order_by(EmailDelivery.created_at).limit(limit)).all()
    for row in rows:
        user = db.get(User, row.user_id)
        row.attempts += 1
        try:
            row.provider, row.provider_message_id = _deliver(user, render(row.kind, user.full_name, **row.payload))
            row.status = 'provider_accepted' if row.provider == 'brevo' else 'written_to_outbox'
            row.last_error = ''
        except EmailUnavailable as exc:
            row.last_error = str(exc)[:300]
            # Brevo has no idempotency key; a network timeout may have been accepted, so cap retries low.
            permanent = 'status 4' in row.last_error and 'status 429' not in row.last_error
            row.status = 'failed' if permanent or row.attempts >= MAX_ATTEMPTS else 'retrying'
    return len(rows)
