import hashlib
import hmac
import json
import secrets
from datetime import timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import emails
from .config import settings
from .db import get_db
from .models import Audit, BrowserSession, RateBucket, User, Verification, now, uid

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def code_digest(user_id: str, purpose: str, code: str) -> str:
    return hmac.new(settings().app_secret.encode(), f'{user_id}:{purpose}:{code}'.encode(), hashlib.sha256).hexdigest()


def check_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def fail(status: int, message: str):
    raise HTTPException(status, message)


def throttle(db: Session, key: str, limit=5, seconds=900):
    # Atomic upsert avoids races on the first request from an address.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    key = digest(key)
    insert = sqlite_insert if db.bind.dialect.name == 'sqlite' else pg_insert
    db.execute(insert(RateBucket).values(key=key, hits=0, reset_at=now() + timedelta(seconds=seconds)).on_conflict_do_nothing(index_elements=['key']))
    bucket = db.scalar(select(RateBucket).where(RateBucket.key == key).with_for_update())
    if aware(bucket.reset_at) <= now():
        bucket.hits, bucket.reset_at = 0, now() + timedelta(seconds=seconds)
    bucket.hits += 1
    blocked = bucket.hits > limit
    db.commit()  # Persist attempts even when the subsequent operation fails.
    if blocked:
        fail(429, 'Too many attempts. Please wait before trying again.')


def ip_key(request: Request) -> str:
    # Never trust unconfigured forwarded headers.
    return request.client.host if request.client else 'unknown'


def university_directory():
    return json.loads(Path(settings().university_directory).read_text(encoding='utf-8'))


def validate_university(university: str, email: str):
    rows = university_directory()
    item = next((r for r in rows if r['name'] == university), None)
    if not item or (settings().environment == 'production' and university == 'Demo University'):
        fail(422, 'Choose a supported university.')
    if email.lower().split('@')[-1] not in item['domains']:
        fail(422, 'Use a student email in your university’s configured domain policy. This verifies email access, not enrolment.')


def set_session_cookies(response: Response, access: str, refresh: str, csrf: str):
    cfg = settings()
    options = dict(secure=cfg.cookie_secure, samesite='lax', path='/api')
    response.set_cookie('cc_access', access, httponly=True, max_age=cfg.session_minutes * 60, **options)
    response.set_cookie('cc_refresh', refresh, httponly=True, max_age=cfg.refresh_days * 86400, **options)
    response.set_cookie('cc_csrf', csrf, httponly=False, secure=cfg.cookie_secure, samesite='lax', path='/', max_age=cfg.refresh_days * 86400)


def issue_session(db: Session, user: User, response: Response, existing: BrowserSession | None = None):
    access, refresh, csrf = (secrets.token_urlsafe(32) for _ in range(3))
    row = existing or BrowserSession(user_id=user.id)
    row.access_hash, row.refresh_hash, row.csrf_hash = digest(access), digest(refresh), digest(csrf)
    row.expires_at = now() + timedelta(minutes=settings().session_minutes)
    if existing is None:
        row.refresh_expires_at = now() + timedelta(days=settings().refresh_days)
        db.add(row)
    set_session_cookies(response, access, refresh, csrf)


def session_user(request: Request, db: Session = Depends(get_db)) -> User:
    row = db.scalar(select(BrowserSession).where(BrowserSession.access_hash == digest(request.cookies.get('cc_access', ''))))
    if not row or row.revoked or aware(row.expires_at) <= now() or aware(row.refresh_expires_at) <= now():
        fail(401, 'Your session has expired. Please sign in.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        csrf_check(request, row)
    request.state.browser_session = row
    user = db.get(User, row.user_id)
    if not user or not user.email_verified:
        fail(403, 'Verify your email to continue.')
    return user


def csrf_check(request: Request, row: BrowserSession):
    token = request.headers.get('x-csrf-token', '')
    if not token or not hmac.compare_digest(row.csrf_hash, digest(token)):
        fail(403, 'Security check failed. Refresh the page and try again.')


def user_view(user: User):
    return dict(id=user.id, full_name=user.full_name, email=user.email, phone=user.phone,
                university=user.university, department=user.department, email_verified=user.email_verified,
                student_status='Email access verified; enrolment not independently verified',
                kyc_status='not_available', notification_preferences=user.notification_preferences)


def send_code(db: Session, user: User, purpose: str):
    previous = db.scalar(select(Verification).where(Verification.user_id == user.id, Verification.purpose == purpose).order_by(Verification.created_at.desc()).limit(1))
    if previous and (now() - aware(previous.created_at)).total_seconds() < 60:
        fail(429, 'Please wait 60 seconds before requesting another code.')
    code = f'{secrets.randbelow(1_000_000):06d}'
    cfg = settings()
    try:
        row = emails.send_now(db, user, purpose, f'{purpose}:{uid()}', code=code, minutes=cfg.otp_minutes, link='/verify' if purpose == 'verify' else '/reset-password')
    except emails.EmailUnavailable:
        fail(503, 'Email delivery is unavailable right now. Your account is saved; please request a new code later.')
    delivery = 'provider_accepted' if row.status == 'provider_accepted' else 'development_outbox'
    db.execute(update(Verification).where(Verification.user_id == user.id, Verification.purpose == purpose).values(used=True))
    db.add(Verification(user_id=user.id, purpose=purpose, code_hash=code_digest(user.id, purpose, code), expires_at=now() + timedelta(minutes=cfg.otp_minutes)))
    return delivery


def consume_code(db: Session, user: User | None, purpose: str, code: str):
    if user is None:
        fail(400, 'The code is invalid or expired.')
    row = db.scalar(select(Verification).where(Verification.user_id == user.id, Verification.purpose == purpose, Verification.used.is_(False)).order_by(Verification.created_at.desc()).with_for_update().limit(1))
    if not row or row.attempts >= 5 or aware(row.expires_at) <= now():
        fail(400, 'The code is invalid or expired. Request a new code.')
    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, code_digest(user.id, purpose, code)):
        db.commit()
        fail(400, 'The code is invalid or expired.')
    row.used = True


def audit(db: Session, actor_id: str | None, action: str, entity_id: str, circle_id: str | None = None, **details):
    db.add(Audit(actor_id=actor_id, action=action, entity_id=entity_id, circle_id=circle_id, details=details))
