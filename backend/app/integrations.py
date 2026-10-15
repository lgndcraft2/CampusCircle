"""Circle sandbox treasury (B Moni) and provider webhooks. Sandbox only; never mixed with simulated funds."""
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import bmoni
from .community import member, notify_circle
from .config import settings
from .db import get_db
from .finance import post_journal
from .models import Circle, Membership, ProviderAccount, User, WebhookEvent
from .security import audit, fail, session_user, throttle

log = logging.getLogger('campuscircle.integrations')
router = APIRouter(prefix='/api/v1')
client_factory = bmoni.BmoniClient  # Replaced in tests with a client using a mock transport.


def treasury_view(row: ProviderAccount | None, admin: bool):
    cfg = settings()
    base = {'provider': 'bmoni', 'environment': 'sandbox', 'configured': cfg.bmoni_status == 'sandbox_configured',
            'notice': 'B Moni SANDBOX: test tokens only. KYC, virtual accounts, and payouts are not enabled in CampusCircle.'}
    if not row:
        return {**base, 'status': 'not_provisioned'}
    data = {**base, 'status': row.status, 'wallet_address': row.wallet_address, 'currency': bmoni.WALLET_CURRENCY, 'updated_at': row.updated_at}
    if admin:
        data.update(external_user_id=row.external_user_id, wallet_id=row.wallet_id, owner_address=row.owner_address, last_error=row.last_error)
    return data


@router.get('/circles/{circle_id}/treasury')
def treasury(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    _, own = member(db, circle_id, user)
    row = db.scalar(select(ProviderAccount).where(ProviderAccount.provider == 'bmoni', ProviderAccount.circle_id == circle_id))
    return treasury_view(row, own.role in ('owner', 'admin'))


def synthetic_identity(circle_id: str):
    """Deterministic, obviously synthetic contact details for the circle's sandbox treasury user."""
    digits = int(hashlib.sha256(circle_id.encode()).hexdigest(), 16) % 10**8
    return f'treasury+{circle_id}@example.com', f'+23490{digits:08d}'


@router.post('/circles/{circle_id}/treasury')
def provision_treasury(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    if settings().bmoni_status != 'sandbox_configured':
        fail(503, 'The B Moni sandbox is not configured on this server. Simulated circle funds remain available.')
    throttle(db, f'treasury:{circle_id}', limit=5, seconds=300)
    circle, _ = member(db, circle_id, user, owner=True, lock=True)
    row = db.scalar(select(ProviderAccount).where(ProviderAccount.provider == 'bmoni', ProviderAccount.circle_id == circle_id))
    account = bmoni.owner_account(circle_id)
    if row and row.status == 'active':
        return treasury_view(row, True)
    if not row:
        row = ProviderAccount(provider='bmoni', circle_id=circle_id, owner_address=account.address)
        db.add(row)
        db.flush()
    email, phone = synthetic_identity(circle_id)
    client = client_factory()
    try:
        if not row.external_user_id:
            created = client.create_user('CampusCircle', 'Treasury', email, phone)
            if not created.ok:
                # Conflict or uncertainty: the user may already exist from an earlier attempt. Reconcile before giving up.
                found = client.find_user_by_email(email) if created.kind == 'uncertain' or created.status_code == 409 else created
                if not found.ok:
                    row.status, row.last_error = ('uncertain' if created.kind == 'uncertain' else 'failed'), created.message
                    audit(db, user.id, 'treasury.user_failed', row.id, circle_id, outcome=created.kind)
                    db.commit()
                    fail(502, f'B Moni sandbox user could not be created: {created.message}')
                created = found
            row.external_user_id = created.extra['bmoni_user_id']
            row.status = 'user_created'
            db.flush()
        wallet = client.provision_wallet(row.external_user_id, account)
        if not wallet.ok:
            row.status, row.last_error = ('uncertain' if wallet.kind == 'uncertain' else 'wallet_failed'), wallet.message
            if wallet.kind == 'uncertain':
                listed = client.list_wallets(row.external_user_id)
                existing = next((w for w in (listed.data or []) if isinstance(w, dict) and w.get('currency') in ('NGN', bmoni.WALLET_CURRENCY)), None) if listed.ok and isinstance(listed.data, list) else None
                if existing:
                    row.wallet_id, row.wallet_address, row.status, row.last_error = existing.get('id'), existing.get('walletAddress'), 'active', ''
            if row.status != 'active':
                audit(db, user.id, 'treasury.wallet_failed', row.id, circle_id, outcome=wallet.kind)
                db.commit()
                fail(502, f'B Moni sandbox wallet was not provisioned: {wallet.message}')
        else:
            row.wallet_id, row.wallet_address, row.status, row.last_error = wallet.extra['wallet_id'], wallet.extra.get('wallet_address'), 'active', ''
    finally:
        client.close()
    audit(db, user.id, 'treasury.provisioned', row.id, circle_id, provider='bmoni', environment='sandbox')
    notify_circle(db, circle_id, 'Sandbox treasury connected', f'{circle.name} now has a B Moni SANDBOX wallet. Test tokens only; no real money.', 'circle')
    return treasury_view(row, True)


@router.get('/circles/{circle_id}/treasury/balance')
def treasury_balance(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True)
    row = db.scalar(select(ProviderAccount).where(ProviderAccount.provider == 'bmoni', ProviderAccount.circle_id == circle_id))
    if not row or not row.external_user_id or row.status != 'active':
        fail(409, 'This circle has no active sandbox treasury.')
    if settings().bmoni_status != 'sandbox_configured':
        fail(503, 'The B Moni sandbox is not configured on this server.')
    throttle(db, f'treasury-balance:{circle_id}', limit=20, seconds=60)
    client = client_factory()
    try:
        result = client.balances(row.external_user_id)
    finally:
        client.close()
    if not result.ok:
        fail(502, f'B Moni balance could not be read: {result.message}')
    # Provider-reported, read-only. Not written to the ledger; only signed webhooks create sandbox journals.
    return {'provider': 'bmoni', 'environment': 'sandbox', 'source': 'provider_reported', 'balances': result.extra['balances']}


@router.post('/webhooks/bmoni', include_in_schema=True)
async def bmoni_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not bmoni.verify_webhook(raw, request.headers.get('x-webhook-signature', ''), settings().bmoni_webhook_secret):
        return JSONResponse({'detail': 'Invalid signature.'}, status_code=401)
    try:
        event = json.loads(raw)
        event_id, event_type = str(event['id']), str(event['eventType'])
        payload = event.get('payload') or {}
    except (ValueError, KeyError, TypeError):
        return JSONResponse({'detail': 'Malformed event.'}, status_code=400)
    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == 'bmoni', WebhookEvent.event_id == event_id)):
        return {'received': True, 'duplicate': True}
    row = WebhookEvent(provider='bmoni', event_id=event_id, event_type=event_type, payload={'userId': payload.get('userId'), 'amount': payload.get('amount'), 'currency': payload.get('currency')})
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {'received': True, 'duplicate': True}
    process_event(db, row, payload)
    return {'received': True, 'status': row.status}


def process_event(db: Session, row: WebhookEvent, payload: dict):
    if row.event_type != 'employee.deposit.completed':
        row.status, row.outcome = 'ignored', 'Event type is recorded but not acted on.'
        return
    account = db.scalar(select(ProviderAccount).where(ProviderAccount.provider == 'bmoni', ProviderAccount.external_user_id == str(payload.get('userId'))))
    if not account:
        row.status, row.outcome = 'unmatched', 'No circle treasury matches this B Moni user.'
        return
    currency = payload.get('currency')
    if currency not in (None, 'NGN', 'CNGN'):
        row.status, row.outcome = 'rejected', 'Only NGN/CNGN sandbox deposits are recorded.'
        return
    try:
        amount = bmoni.parse_amount_minor(payload.get('amount'))
    except ValueError as exc:
        row.status, row.outcome = 'rejected', str(exc)
        return
    db.scalar(select(Circle).where(Circle.id == account.circle_id).with_for_update())
    owner = db.scalar(select(Membership).where(Membership.circle_id == account.circle_id, Membership.role == 'owner', Membership.status == 'active'))
    journal = post_journal(db, account.circle_id, owner.user_id, f'bmoni:{row.event_id}'[:100], 'sandbox_deposit', amount,
                           [('bmoni_sandbox_cash', amount), ('bmoni_sandbox_clearing', -amount)], status='provider_confirmed', provenance='bmoni_sandbox')
    row.status, row.outcome, row.journal_id = 'processed', 'Sandbox deposit recorded.', journal.id
    notify_circle(db, account.circle_id, 'Sandbox deposit confirmed', 'B Moni confirmed a SANDBOX deposit to your circle treasury. Test tokens only.', 'finance')
