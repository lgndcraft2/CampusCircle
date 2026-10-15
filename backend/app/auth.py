from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import schemas as s
from .db import get_db
from .community import notify
from .models import BrowserSession, User, now
from .security import (DUMMY_HASH, audit, aware, check_password, consume_code, csrf_check, digest, fail, hasher, ip_key, issue_session,
                       send_code, session_user, throttle, university_directory, user_view, validate_university)

router = APIRouter(prefix='/api/v1')


@router.get('/universities')
def universities():
    return university_directory()


@router.post('/auth/register', status_code=201)
def register(data: s.Register, request: Request, db: Session = Depends(get_db)):
    throttle(db, f'register:{ip_key(request)}', limit=10)
    email = str(data.email).lower()
    validate_university(data.university, email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        return {'message': 'If this address is eligible, a verification code will be sent. You can also request a new code.', 'delivery': 'check_email'}
    user = User(**data.model_dump(exclude={'password', 'email'}), email=email, password_hash=hasher.hash(data.password))
    db.add(user)
    db.flush()
    audit(db, user.id, 'account.registered', user.id)
    db.commit()  # Account survives delivery failures so resend can recover.
    delivery = send_code(db, user, 'verify')
    return {'message': 'Account created. Verify your email before signing in.', 'delivery': delivery}


@router.post('/auth/login')
def login(data: s.Login, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(db, f'login-ip:{ip_key(request)}', limit=30)
    throttle(db, f'login-account:{str(data.email).lower()}', limit=10)
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    valid = check_password(user.password_hash if user else DUMMY_HASH, data.password)
    if not user or not valid:
        fail(401, 'Email or password is incorrect.')
    if not user.email_verified:
        fail(403, 'Verify your email before signing in.')
    issue_session(db, user, response)
    audit(db, user.id, 'session.created', user.id)
    return user_view(user)


@router.post('/auth/verify')
def verify(data: s.Verify, request: Request, db: Session = Depends(get_db)):
    throttle(db, f'verify:{ip_key(request)}', limit=20)
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    consume_code(db, user, 'verify', data.code)
    user.email_verified = True
    audit(db, user.id, 'email.verified', user.id)
    return {'message': 'Email verified. You can now sign in.'}


@router.post('/auth/resend')
def resend(data: s.EmailInput, request: Request, db: Session = Depends(get_db)):
    throttle(db, f'resend:{ip_key(request)}', limit=10)
    throttle(db, f'resend-email:{str(data.email).lower()}', limit=5)
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    if user and not user.email_verified:
        send_code(db, user, 'verify')
    return {'message': 'If the account needs verification, a new code has been requested.'}


@router.post('/auth/forgot-password')
def forgot(data: s.EmailInput, request: Request, db: Session = Depends(get_db)):
    throttle(db, f'reset-ip:{ip_key(request)}', limit=10)
    throttle(db, f'reset-email:{str(data.email).lower()}', limit=5)
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    if user:
        send_code(db, user, 'reset')
    return {'message': 'If the account exists, a reset code has been requested.'}


@router.post('/auth/reset-password')
def reset(data: s.Reset, request: Request, db: Session = Depends(get_db)):
    throttle(db, f'reset-complete:{ip_key(request)}', limit=20)
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    consume_code(db, user, 'reset', data.code)
    user.password_hash = hasher.hash(data.password)
    db.execute(update(BrowserSession).where(BrowserSession.user_id == user.id).values(revoked=True))
    notify(db, user.id, 'Password changed', 'Your password was reset and all sessions were revoked. If this was not you, reset it again immediately.', category='security')
    audit(db, user.id, 'password.reset', user.id)
    return {'message': 'Password updated. Sign in with your new password.'}


@router.post('/auth/refresh')
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    row = db.scalar(select(BrowserSession).where(BrowserSession.refresh_hash == digest(request.cookies.get('cc_refresh', ''))).with_for_update())
    if not row or row.revoked or aware(row.refresh_expires_at) <= now():
        fail(401, 'Your session expired. Please sign in.')
    csrf_check(request, row)
    user = db.get(User, row.user_id)
    issue_session(db, user, response, row)
    return {'message': 'Session refreshed.'}


@router.post('/auth/logout')
def logout(request: Request, response: Response, user: User = Depends(session_user), db: Session = Depends(get_db)):
    request.state.browser_session.revoked = True
    for cookie in ('cc_access', 'cc_refresh'):
        response.delete_cookie(cookie, path='/api')
    response.delete_cookie('cc_csrf', path='/')
    audit(db, user.id, 'session.revoked', request.state.browser_session.id)
    return {'message': 'Signed out.'}


@router.get('/me')
def me(user: User = Depends(session_user)):
    return user_view(user)


@router.patch('/me')
def profile(data: s.Profile, user: User = Depends(session_user), db: Session = Depends(get_db)):
    user.full_name, user.department = data.full_name, data.department
    audit(db, user.id, 'profile.updated', user.id)
    return user_view(user)


@router.post('/me/phone-change')
def phone_change(user: User = Depends(session_user)):
    fail(503, 'Phone changes require an SMS verification provider, which is not configured. Your current number has been kept.')


@router.patch('/me/preferences')
def preferences(data: s.Preferences, user: User = Depends(session_user)):
    user.notification_preferences = data.model_dump()
    return user_view(user)


@router.get('/me/sessions')
def sessions(request: Request, user: User = Depends(session_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(BrowserSession).where(BrowserSession.user_id == user.id, BrowserSession.revoked.is_(False), BrowserSession.refresh_expires_at > now()).order_by(BrowserSession.created_at.desc()).limit(100)).all()
    return [{'id': r.id, 'created_at': r.created_at, 'expires_at': r.refresh_expires_at, 'current': r.id == request.state.browser_session.id} for r in rows]


@router.delete('/me/sessions/{session_id}')
def revoke_session(session_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    row = db.get(BrowserSession, session_id)
    if not row or row.user_id != user.id:
        fail(404, 'Session not found.')
    row.revoked = True
    return {'message': 'Session revoked.'}
