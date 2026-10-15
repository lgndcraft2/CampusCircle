import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, community, copilot, finance, gamification, integrations
from .config import settings
from .db import get_db
from .models import Circle, Goal, Journal, Loan, Membership, Notification, User
from .security import session_user


class JsonFormatter(logging.Formatter):
    RESERVED = set(vars(logging.makeLogRecord({})))

    def format(self, record):
        data = {'level': record.levelname, 'logger': record.name, 'event': record.getMessage(), 'time': self.formatTime(record)}
        data.update({k: v for k, v in vars(record).items() if k not in self.RESERVED and not k.startswith('_')})
        return json.dumps(data, default=str)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=settings().log_level, handlers=[handler], force=True)
log = logging.getLogger('campuscircle.http')
REQUEST_ID = re.compile(r'^[A-Za-z0-9-]{8,64}$')


@asynccontextmanager
async def lifespan(_):
    settings().validate_runtime()
    yield


app = FastAPI(title='CampusCircle API', version='1.0.0', lifespan=lifespan, docs_url='/api/v1/docs', openapi_url='/api/v1/openapi.json')
app.add_middleware(CORSMiddleware, allow_origins=[settings().frontend_url], allow_credentials=True,
                   allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token', 'Idempotency-Key', 'X-Request-ID'],
                   expose_headers=['X-Request-ID'])


@app.middleware('http')
async def security_headers(request: Request, call_next):
    incoming = request.headers.get('x-request-id', '')
    request_id = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != settings().frontend_url:
            return JSONResponse({'detail': 'Origin is not permitted.', 'request_id': request_id}, status_code=403, headers={'X-Request-ID': request_id})
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            length = 0
        if length > 262144:
            return JSONResponse({'detail': 'Request is too large.', 'request_id': request_id}, status_code=413, headers={'X-Request-ID': request_id})
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
                             'Cache-Control': 'no-store', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
                             'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'" if not request.url.path.endswith('/docs') else "frame-ancestors 'none'",
                             'X-Request-ID': request_id})
    # Paths only: never query strings, bodies, cookies, or codes.
    log.info('request', extra={'request_id': request_id, 'method': request.method, 'path': request.url.path, 'status': response.status_code,
                               'duration_ms': int((time.monotonic() - started) * 1000)})
    return response


def error_body(request: Request, detail):
    return {'detail': detail, 'request_id': getattr(request.state, 'request_id', None)}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(error_body(request, exc.detail), status_code=exc.status_code, headers=getattr(exc, 'headers', None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc):
    # Never echo passwords, codes, or arbitrary submitted content in error payloads.
    return JSONResponse(error_body(request, [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]), status_code=422)


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, __):
    return JSONResponse(error_body(request, 'This operation conflicts with an existing record. Refresh and try again.'), status_code=409)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.error('unhandled', extra={'request_id': getattr(request.state, 'request_id', None), 'error': type(exc).__name__})
    return JSONResponse(error_body(request, 'Something went wrong on our side. Nothing was changed. Please try again.'), status_code=500)


for module in (auth, community, finance, copilot, gamification, integrations):
    app.include_router(module.router)


@app.get('/api/v1/health')
def health(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    cfg = settings()
    return {'status': 'ok', 'version': app.version, 'financial_mode': 'simulation', 'database': db.bind.dialect.name,
            'email': cfg.email_provider, 'ai': cfg.ai_provider, 'ai_model': cfg.groq_model if cfg.ai_provider == 'groq' else None,
            'bmoni': cfg.bmoni_status, 'bmoni_environment': 'sandbox'}


@app.get('/api/v1/dashboard')
def dashboard(user: User = Depends(session_user), db: Session = Depends(get_db)):
    ids = list(db.scalars(select(Membership.circle_id).where(Membership.user_id == user.id, Membership.status == 'active')))
    circles = db.scalars(select(Circle).where(Circle.id.in_(ids)).order_by(Circle.created_at.desc())).all()
    contributed = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.user_id == user.id, Journal.kind == 'contribution'))
    reversals = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.kind == 'contribution_reversal', Journal.reversal_of.in_(select(Journal.id).where(Journal.user_id == user.id, Journal.kind == 'contribution'))))
    loans = db.scalars(select(Loan).where(Loan.circle_id.in_(ids)).order_by(Loan.created_at.desc()).limit(50)).all()
    views = [finance.loan_view(db, loan, user) for loan in loans]
    own = [v for v in views if v['borrower_id'] == user.id and v['status'] in ('disbursed', 'repaying')]
    needs_me = [v for v in views if (v['status'] == 'pending_vote' and v['can_vote'] and not v['my_vote'])
                or (v['borrower_id'] == user.id and v['status'] in ('approved_awaiting_acceptance', 'approved_insufficient_funds'))]
    recent = db.scalars(select(Journal).where(Journal.circle_id.in_(ids)).order_by(Journal.created_at.desc()).limit(6))
    names = dict(db.execute(select(Circle.id, Circle.name).where(Circle.id.in_(ids))).all())
    goals = []
    for g in db.scalars(select(Goal).where(Goal.circle_id.in_(ids), Goal.status == 'active').order_by(Goal.target_date).limit(4)):
        progress = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.goal_id == g.id, Journal.kind == 'contribution'))
        progress -= db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.goal_id == g.id, Journal.kind == 'contribution_reversal'))
        goals.append({'id': g.id, 'circle_id': g.circle_id, 'circle_name': names.get(g.circle_id, ''), 'name': g.name, 'target_minor': g.target_minor, 'progress_minor': progress, 'target_date': g.target_date})
    pending = db.execute(select(Circle.id, Circle.name).join(Membership, Membership.circle_id == Circle.id).where(Membership.user_id == user.id, Membership.status == 'pending')).all()
    notices = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(4))
    return {'contributed_minor': contributed - reversals, 'circle_count': len(circles), 'outstanding_minor': sum(v['outstanding_minor'] for v in own),
            'available_minor': sum(community.balances(db, c.id)['available_minor'] for c in circles),
            'email_verified': user.email_verified, 'pending_votes': needs_me, 'repayments': own, 'goals': goals,
            'pending_requests': [{'circle_id': cid, 'circle_name': name} for cid, name in pending],
            'notifications': [dict(id=n.id, title=n.title, body=n.body, link=n.link, read=n.read, created_at=n.created_at) for n in notices],
            'circles': [community.circle_view(db, c, user) for c in circles],
            'transactions': [finance.journal_view(r, db) for r in recent], 'provenance': 'simulation'}
