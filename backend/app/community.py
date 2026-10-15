import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import emails, schemas as s
from .config import settings
from .db import get_db
from .models import Audit, Circle, Entry, Goal, Invitation, Journal, Loan, Membership, Message, Notification, User, now
from .security import audit, aware, digest, fail, session_user

router = APIRouter(prefix='/api/v1')


def member(db: Session, circle_id: str, user: User, admin=False, owner=False, lock=False):
    circle = db.scalar(select(Circle).where(Circle.id == circle_id).with_for_update()) if lock else db.get(Circle, circle_id)
    row = db.scalar(select(Membership).where(Membership.circle_id == circle_id, Membership.user_id == user.id, Membership.status == 'active'))
    if not circle or not row:
        fail(404, 'Circle not found or you do not have access.')
    if (admin and row.role not in ('owner', 'admin')) or (owner and row.role != 'owner'):
        fail(403, 'Your circle role does not permit this action.')
    return circle, row


def active_members(db: Session, circle_id: str):
    return db.scalars(select(Membership).where(Membership.circle_id == circle_id, Membership.status == 'active')).all()


def notify(db: Session, user_id: str, title: str, body: str, circle_id: str | None = None, category='finance'):
    user = db.get(User, user_id)
    if category == 'security' or user.notification_preferences.get(category, True):
        row = Notification(user_id=user_id, title=title, body=body, category=category, link=f'/app/circles/{circle_id}' if circle_id else '/app/profile')
        db.add(row)
        db.flush()
        if category in ('circle', 'finance', 'security'):
            emails.enqueue(db, user, category, f'notification:{row.id}', title, body, row.link)


def notify_circle(db: Session, circle_id: str, title: str, body: str, category='finance'):
    for row in active_members(db, circle_id):
        notify(db, row.user_id, title, body, circle_id, category)


def balances(db: Session, circle_id: str):
    cash = db.scalar(select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(Entry.circle_id == circle_id, Entry.account == 'cash'))
    reserved = db.scalar(select(func.coalesce(func.sum(Loan.principal_minor), 0)).where(Loan.circle_id == circle_id, Loan.status == 'disbursement_pending'))
    # Provider-confirmed sandbox deposits live in their own account and never fund simulated loans.
    sandbox = db.scalar(select(func.coalesce(func.sum(Entry.amount_minor), 0)).where(Entry.circle_id == circle_id, Entry.account == 'bmoni_sandbox_cash'))
    return {'cash_minor': cash, 'reserved_minor': reserved, 'available_minor': cash - reserved, 'provenance': 'simulation', 'currency': 'NGN',
            'sandbox_cash_minor': sandbox}


def circle_view(db: Session, circle: Circle, user: User):
    row = db.scalar(select(Membership).where(Membership.circle_id == circle.id, Membership.user_id == user.id))
    count = db.scalar(select(func.count()).select_from(Membership).where(Membership.circle_id == circle.id, Membership.status == 'active'))
    data = {key: getattr(circle, key) for key in ('id', 'name', 'description', 'purpose', 'university', 'department', 'visibility', 'joining_method', 'contribution_frequency', 'contribution_target', 'membership_limit', 'rules', 'invitations_enabled')}
    data.update(member_count=count, membership_status=row.status if row else None, role=row.role if row and row.status == 'active' else None)
    if row and row.status == 'active':
        unread = db.scalar(select(func.count()).select_from(Message).where(Message.circle_id == circle.id, Message.sender_id != user.id, Message.created_at > row.last_read_at))
        data.update(**balances(db, circle.id), unread_count=unread)
    return data


def ensure_eligible(circle: Circle, user: User):
    if circle.university != user.university or (circle.department and circle.department != user.department):
        fail(403, 'This circle is restricted to its university and department.')


def activate(db: Session, circle: Circle, user: User):
    ensure_eligible(circle, user)
    row = db.scalar(select(Membership).where(Membership.circle_id == circle.id, Membership.user_id == user.id))
    if row and row.status == 'active':
        return row
    if len(active_members(db, circle.id)) >= circle.membership_limit:
        fail(409, 'This circle has reached its membership limit.')
    if row:
        row.status, row.role = 'active', 'member'
    else:
        row = Membership(circle_id=circle.id, user_id=user.id)
        db.add(row)
    db.flush()
    audit(db, user.id, 'membership.joined', row.id, circle.id)
    notify(db, user.id, 'You’re in the circle', f'Your membership in {circle.name} is active.', circle.id, 'circle')
    return row


@router.get('/circles')
def circles(scope: str = 'mine', q: str = Query('', max_length=100), university: str = '', department: str = '', purpose: str = '', available: bool = False,
            page: int = Query(1, ge=1), limit: int = Query(12, ge=1, le=50), user: User = Depends(session_user), db: Session = Depends(get_db)):
    stmt = select(Circle)
    if scope == 'mine':
        stmt = stmt.join(Membership, Membership.circle_id == Circle.id).where(Membership.user_id == user.id, Membership.status.in_(['active', 'pending']))
    elif scope == 'discover':
        stmt = stmt.where(Circle.visibility == 'discoverable', Circle.university == user.university, or_(Circle.department == '', Circle.department == user.department))
    else:
        fail(422, 'Choose mine or discover.')
    if q:
        stmt = stmt.where(or_(Circle.name.ilike(f'%{q}%'), Circle.purpose.ilike(f'%{q}%')))
    for column, value in ((Circle.university, university), (Circle.department, department), (Circle.purpose, purpose)):
        if value:
            stmt = stmt.where(column.ilike(f'%{value}%'))
    if available:
        count = select(func.count(Membership.id)).where(Membership.circle_id == Circle.id, Membership.status == 'active').correlate(Circle).scalar_subquery()
        stmt = stmt.where(count < Circle.membership_limit)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Circle.created_at.desc(), Circle.id).offset((page - 1) * limit).limit(limit)).all()
    return {'items': [circle_view(db, c, user) for c in rows], 'total': total, 'page': page, 'limit': limit}


@router.post('/circles', status_code=201)
def create_circle(data: s.CircleInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    if data.university != user.university or (data.department and data.department != user.department):
        fail(422, 'Create circles for your own university and department.')
    circle = Circle(**data.model_dump())
    db.add(circle)
    db.flush()
    db.add(Membership(circle_id=circle.id, user_id=user.id, role='owner'))
    audit(db, user.id, 'circle.created', circle.id, circle.id)
    db.flush()
    return circle_view(db, circle, user)


@router.get('/circles/{circle_id}')
def circle_detail(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    circle, _ = member(db, circle_id, user)
    return circle_view(db, circle, user)


@router.patch('/circles/{circle_id}')
def edit_circle(circle_id: str, data: s.CircleInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    circle, _ = member(db, circle_id, user, owner=True, lock=True)
    if data.university != circle.university or data.department != circle.department:
        fail(422, 'University and department restrictions cannot change after creation.')
    if data.membership_limit < len(active_members(db, circle_id)):
        fail(422, 'The membership limit cannot be lower than the current membership.')
    for key, value in data.model_dump().items():
        setattr(circle, key, value)
    audit(db, user.id, 'circle.settings_updated', circle_id, circle_id)
    return circle_view(db, circle, user)


@router.post('/circles/{circle_id}/join')
def join(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    circle = db.scalar(select(Circle).where(Circle.id == circle_id).with_for_update())
    if not circle or circle.visibility != 'discoverable':
        fail(404, 'Circle not found.')
    ensure_eligible(circle, user)
    if circle.joining_method == 'invitation':
        fail(403, 'An invitation is required.')
    if circle.joining_method == 'open':
        activate(db, circle, user)
    else:
        row = db.scalar(select(Membership).where(Membership.circle_id == circle_id, Membership.user_id == user.id))
        if not row:
            db.add(Membership(circle_id=circle_id, user_id=user.id, status='pending'))
        elif row.status != 'active':
            row.status, row.role = 'pending', 'member'
        for admin in active_members(db, circle_id):
            if admin.role in ('owner', 'admin'):
                notify(db, admin.user_id, 'Membership request', f'{user.full_name} would like to join {circle.name}.', circle_id, 'circle')
    db.flush()
    return circle_view(db, circle, user)


@router.get('/circles/{circle_id}/members')
def members(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    _, own = member(db, circle_id, user)
    stmt = select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.circle_id == circle_id)
    stmt = stmt.where(Membership.status.in_(['active', 'pending']) if own.role in ('owner', 'admin') else Membership.status == 'active')
    return [{'id': m.id, 'user_id': u.id, 'full_name': u.full_name, 'department': u.department, 'role': m.role, 'status': m.status} for m, u in db.execute(stmt.order_by(Membership.created_at)).all()]


def check_can_leave(db: Session, circle_id: str, user_id: str):
    loans = db.scalars(select(Loan).where(Loan.circle_id == circle_id, Loan.status.notin_(['rejected', 'expired', 'cancelled', 'repaid']))).all()
    if any(loan.borrower_id == user_id or (loan.status == 'pending_vote' and user_id in loan.eligible_voters) for loan in loans):
        fail(409, 'Membership must remain active while you have an outstanding loan or an eligible vote.')


@router.post('/circles/{circle_id}/members/{membership_id}')
def manage_member(circle_id: str, membership_id: str, data: s.MemberAction, user: User = Depends(session_user), db: Session = Depends(get_db)):
    circle, own = member(db, circle_id, user, admin=True, lock=True)
    target = db.get(Membership, membership_id)
    if not target or target.circle_id != circle_id:
        fail(404, 'Member not found.')
    if target.role == 'owner' or target.user_id == user.id:
        fail(403, 'Use ownership transfer or the leave flow for this action.')
    if data.action in ('admin', 'member', 'transfer_ownership') and own.role != 'owner':
        fail(403, 'Only the owner may manage roles.')
    if own.role == 'admin' and target.role == 'admin':
        fail(403, 'Only the owner may manage another administrator.')
    if data.action in ('approve', 'reject'):
        if target.status != 'pending':
            fail(409, 'This membership request has already been handled.')
        if data.action == 'approve':
            activate(db, circle, db.get(User, target.user_id))
        else:
            target.status = 'rejected'
            notify(db, target.user_id, 'Membership request declined', f'Your request for {circle.name} was declined.', circle_id, 'circle')
    elif data.action == 'remove':
        check_can_leave(db, circle_id, target.user_id)
        target.status = 'removed'
    else:
        if target.status != 'active':
            fail(409, 'The member must be active.')
        if data.action == 'transfer_ownership':
            own.role, target.role = 'admin', 'owner'
        else:
            target.role = data.action
    audit(db, user.id, f'membership.{data.action}', target.id, circle_id)
    return {'message': 'Membership updated.'}


@router.post('/circles/{circle_id}/leave')
def leave(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    _, own = member(db, circle_id, user, lock=True)
    if own.role == 'owner':
        fail(409, 'Transfer ownership before leaving.')
    check_can_leave(db, circle_id, user.id)
    own.status = 'left'
    audit(db, user.id, 'membership.left', own.id, circle_id)
    return {'message': 'You left the circle. Pooled contributions remain in its ledger.'}


@router.post('/circles/{circle_id}/invitations')
def invitation(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    circle, _ = member(db, circle_id, user, admin=True, lock=True)
    if not circle.invitations_enabled:
        fail(403, 'Invitations are disabled for this circle.')
    token = secrets.token_urlsafe(32)
    row = Invitation(circle_id=circle_id, creator_id=user.id, token_hash=digest(token), expires_at=now() + timedelta(days=7))
    db.add(row)
    db.flush()
    audit(db, user.id, 'invitation.created', row.id, circle_id)
    return {'id': row.id, 'url': f'{settings().frontend_url}/invite/{token}', 'expires_at': row.expires_at}


@router.get('/circles/{circle_id}/invitations')
def invitations(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True)
    return [{'id': r.id, 'expires_at': r.expires_at, 'revoked': r.revoked} for r in db.scalars(select(Invitation).where(Invitation.circle_id == circle_id).order_by(Invitation.created_at.desc()).limit(100))]


@router.delete('/circles/{circle_id}/invitations/{invitation_id}')
def revoke_invitation(circle_id: str, invitation_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True, lock=True)
    row = db.get(Invitation, invitation_id)
    if not row or row.circle_id != circle_id:
        fail(404, 'Invitation not found.')
    row.revoked = True
    audit(db, user.id, 'invitation.revoked', row.id, circle_id)
    return {'message': 'Invitation revoked.'}


@router.post('/invitations/accept')
def accept_invitation(data: s.JoinInvitation, user: User = Depends(session_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Invitation).where(Invitation.token_hash == digest(data.token)))
    if not row or row.revoked or aware(row.expires_at) <= now():
        fail(400, 'This invitation is invalid or expired.')
    circle = db.scalar(select(Circle).where(Circle.id == row.circle_id).with_for_update())
    if not circle.invitations_enabled:
        fail(403, 'Invitations are disabled.')
    activate(db, circle, user)
    return {'circle_id': circle.id}


@router.get('/circles/{circle_id}/goals')
def goals(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    rows = db.scalars(select(Goal).where(Goal.circle_id == circle_id).order_by(Goal.created_at.desc()).limit(100))
    result = []
    for g in rows:
        progress = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.goal_id == g.id, Journal.kind == 'contribution', Journal.status == 'simulated_confirmed'))
        progress -= db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.goal_id == g.id, Journal.kind == 'contribution_reversal'))
        result.append(dict(id=g.id, name=g.name, description=g.description, target_minor=g.target_minor, progress_minor=progress, start_date=g.start_date, target_date=g.target_date, schedule=g.schedule, status='completed' if progress >= g.target_minor else g.status, eligible_user_ids=g.eligible_user_ids))
    return result


@router.post('/circles/{circle_id}/goals', status_code=201)
def create_goal(circle_id: str, data: s.GoalInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True, lock=True)
    if data.target_date <= data.start_date or data.target_date <= now():
        fail(422, 'The target date must follow the start date and be in the future.')
    eligible = data.eligible_user_ids or [m.user_id for m in active_members(db, circle_id)]
    if not set(eligible).issubset({m.user_id for m in active_members(db, circle_id)}):
        fail(422, 'Only active members can participate in a goal.')
    goal = Goal(circle_id=circle_id, **data.model_dump(exclude={'eligible_user_ids'}), eligible_user_ids=eligible)
    db.add(goal)
    db.flush()
    audit(db, user.id, 'goal.created', goal.id, circle_id)
    return {'id': goal.id}


@router.get('/circles/{circle_id}/messages')
def messages(circle_id: str, page: int = Query(1, ge=1), limit: int = Query(40, ge=1, le=100), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    stmt = select(Message, User.full_name).join(User, User.id == Message.sender_id).where(Message.circle_id == circle_id)
    rows = db.execute(stmt.order_by(Message.created_at.desc(), Message.id.desc()).offset((page - 1) * limit).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(Message).where(Message.circle_id == circle_id))
    return {'items': [{'id': m.id, 'sender_id': m.sender_id, 'sender_name': name, 'body': '' if m.deleted else m.body, 'deleted': m.deleted, 'created_at': m.created_at} for m, name in reversed(rows)], 'total': total, 'page': page, 'limit': limit, 'delivery': 'polling'}


@router.post('/circles/{circle_id}/messages', status_code=201)
def post_message(circle_id: str, data: s.MessageInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    from .security import throttle
    throttle(db, f'chat:{user.id}', limit=30, seconds=60)
    member(db, circle_id, user, lock=True)
    msg = Message(circle_id=circle_id, sender_id=user.id, body=data.body)
    db.add(msg)
    db.flush()
    return {'id': msg.id}


@router.post('/circles/{circle_id}/messages/read')
def read_messages(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    _, own = member(db, circle_id, user)
    own.last_read_at = now()
    return {'message': 'Marked read.'}


@router.delete('/circles/{circle_id}/messages/{message_id}')
def delete_message(circle_id: str, message_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    _, own = member(db, circle_id, user, lock=True)
    msg = db.get(Message, message_id)
    if not msg or msg.circle_id != circle_id:
        fail(404, 'Message not found.')
    if msg.sender_id != user.id and own.role not in ('owner', 'admin'):
        fail(403, 'Only the author or circle administrators can remove a message.')
    msg.deleted, msg.body = True, ''
    audit(db, user.id, 'message.deleted', msg.id, circle_id)
    return {'message': 'Message removed.'}


@router.get('/notifications')
def notifications(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), user: User = Depends(session_user), db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)))
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).offset((page - 1) * limit).limit(limit))
    return {'items': [dict(id=r.id, title=r.title, body=r.body, link=r.link, read=r.read, created_at=r.created_at) for r in rows], 'total': total, 'page': page, 'limit': limit, 'unread': unread}


@router.post('/notifications/{notification_id}/read')
def read_notification(notification_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    row = db.get(Notification, notification_id)
    if not row or row.user_id != user.id:
        fail(404, 'Notification not found.')
    row.read = True
    return {'message': 'Marked read.'}


@router.get('/circles/{circle_id}/activity')
def activity(circle_id: str, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    total = db.scalar(select(func.count()).select_from(Audit).where(Audit.circle_id == circle_id))
    rows = db.scalars(select(Audit).where(Audit.circle_id == circle_id).order_by(Audit.created_at.desc()).offset((page - 1) * limit).limit(limit))
    return {'items': [dict(id=r.id, action=r.action, created_at=r.created_at, actor_id=r.actor_id, details=r.details) for r in rows], 'total': total, 'page': page, 'limit': limit}
