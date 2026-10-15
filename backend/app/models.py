from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid4())


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Record, Base):
    __tablename__ = 'users'
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    university: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_preferences: Mapped[dict] = mapped_column(JSON, default=lambda: {'circle': True, 'finance': True, 'chat': True, 'email': True})


class BrowserSession(Record, Base):
    __tablename__ = 'browser_sessions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Verification(Record, Base):
    __tablename__ = 'verifications'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class RateBucket(Base):
    __tablename__ = 'rate_buckets'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Circle(Record, Base):
    __tablename__ = 'circles'
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(80))
    university: Mapped[str] = mapped_column(String(120), index=True)
    department: Mapped[str] = mapped_column(String(120), default='')
    visibility: Mapped[str] = mapped_column(String(20), default='discoverable')
    joining_method: Mapped[str] = mapped_column(String(20), default='approval')
    contribution_frequency: Mapped[str] = mapped_column(String(20), default='monthly')
    contribution_target: Mapped[int] = mapped_column(BigInteger, default=0)
    membership_limit: Mapped[int] = mapped_column(Integer, default=20)
    invitations_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rules: Mapped[str] = mapped_column(Text)


class Membership(Record, Base):
    __tablename__ = 'memberships'
    __table_args__ = (UniqueConstraint('circle_id', 'user_id'),)
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    role: Mapped[str] = mapped_column(String(20), default='member')
    status: Mapped[str] = mapped_column(String(20), default='active')
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invitation(Record, Base):
    __tablename__ = 'invitations'
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Goal(Record, Base):
    __tablename__ = 'goals'
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    target_minor: Mapped[int] = mapped_column(BigInteger)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schedule: Mapped[str] = mapped_column(String(20), default='monthly')
    eligible_user_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='active')


class Message(Record, Base):
    __tablename__ = 'messages'
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    body: Mapped[str] = mapped_column(Text)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Record, Base):
    __tablename__ = 'notifications'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(String(200), default='/app')
    category: Mapped[str] = mapped_column(String(20), default='finance')
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class LoanRequest(Record, Base):
    __tablename__ = 'loan_requests'
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    borrower_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(String(20))
    instalment_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='requested')


class Loan(Record, Base):
    __tablename__ = 'loans'
    __table_args__ = (UniqueConstraint('request_id', 'version'), CheckConstraint('principal_minor > 0'))
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    request_id: Mapped[str] = mapped_column(ForeignKey('loan_requests.id'))
    borrower_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer)
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    interest_bps: Mapped[int] = mapped_column(Integer)
    interest_minor: Mapped[int] = mapped_column(BigInteger)
    fees_minor: Mapped[int] = mapped_column(BigInteger)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    frequency: Mapped[str] = mapped_column(String(20))
    schedule: Mapped[list] = mapped_column(JSON)
    purpose: Mapped[str] = mapped_column(String(200))
    conditions: Mapped[str] = mapped_column(Text, default='')
    eligible_voters: Mapped[list] = mapped_column(JSON)
    quorum_bps: Mapped[int] = mapped_column(Integer)
    threshold_bps: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acceptance_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default='pending_vote', index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(20), default='simulation')


class Vote(Record, Base):
    __tablename__ = 'votes'
    __table_args__ = (UniqueConstraint('loan_id', 'user_id'),)
    loan_id: Mapped[str] = mapped_column(ForeignKey('loans.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    choice: Mapped[str] = mapped_column(String(10))


class Journal(Record, Base):
    __tablename__ = 'journals'
    __table_args__ = (UniqueConstraint('circle_id', 'idempotency_key'), CheckConstraint('amount_minor > 0'))
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey('goals.id'), nullable=True)
    loan_id: Mapped[str | None] = mapped_column(ForeignKey('loans.id'), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default='NGN')
    status: Mapped[str] = mapped_column(String(30), default='simulated_confirmed')
    provenance: Mapped[str] = mapped_column(String(20), default='simulation')
    idempotency_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    reversal_of: Mapped[str | None] = mapped_column(ForeignKey('journals.id'), nullable=True, unique=True)


class Entry(Record, Base):
    __tablename__ = 'ledger_entries'
    journal_id: Mapped[str] = mapped_column(ForeignKey('journals.id'), index=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    account: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class Audit(Record, Base):
    __tablename__ = 'audit_events'
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    circle_id: Mapped[str | None] = mapped_column(ForeignKey('circles.id'), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Reminder(Record, Base):
    __tablename__ = 'reminders'
    key: Mapped[str] = mapped_column(String(150), unique=True)


class Operation(Record, Base):
    __tablename__ = 'operations'
    __table_args__ = (UniqueConstraint('circle_id', 'key'),)
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36))


class EmailDelivery(Record, Base):
    """A request to the email provider. 'provider_accepted' is not proof of inbox delivery."""
    __tablename__ = 'email_deliveries'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(150), unique=True)
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    provider: Mapped[str] = mapped_column(String(30), default='')
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default='')
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Instalment(Record, Base):
    __tablename__ = 'instalments'
    __table_args__ = (UniqueConstraint('loan_id', 'number'), CheckConstraint('amount_minor > 0'),
                      CheckConstraint('paid_minor >= 0 AND paid_minor <= amount_minor'))
    loan_id: Mapped[str] = mapped_column(ForeignKey('loans.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default='NGN')
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default='upcoming')
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_refs: Mapped[list] = mapped_column(JSON, default=list)


class RepaymentAttempt(Record, Base):
    __tablename__ = 'repayment_attempts'
    __table_args__ = (UniqueConstraint('loan_id', 'idempotency_key'), CheckConstraint('amount_minor > 0'))
    loan_id: Mapped[str] = mapped_column(ForeignKey('loans.id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default='NGN')
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed
    provenance: Mapped[str] = mapped_column(String(20), default='simulation')
    reason: Mapped[str] = mapped_column(String(300), default='')
    journal_id: Mapped[str | None] = mapped_column(ForeignKey('journals.id'), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))


class ProviderAccount(Record, Base):
    """A circle's external sandbox treasury. Holds identifiers only, never keys or identity documents."""
    __tablename__ = 'provider_accounts'
    __table_args__ = (UniqueConstraint('provider', 'circle_id'),)
    provider: Mapped[str] = mapped_column(String(20))
    circle_id: Mapped[str] = mapped_column(ForeignKey('circles.id'), index=True)
    environment: Mapped[str] = mapped_column(String(20), default='sandbox')
    external_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    wallet_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    wallet_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_address: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='pending')
    last_error: Mapped[str] = mapped_column(String(300), default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WebhookEvent(Record, Base):
    __tablename__ = 'webhook_events'
    __table_args__ = (UniqueConstraint('provider', 'event_id'),)
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default='received')
    outcome: Mapped[str] = mapped_column(String(300), default='')
    journal_id: Mapped[str | None] = mapped_column(ForeignKey('journals.id'), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AIInteraction(Record, Base):
    """Metadata only. Questions and answers are not stored."""
    __tablename__ = 'ai_interactions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    source: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80), default='')
    intent: Mapped[str] = mapped_column(String(40), default='general')
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    context_kinds: Mapped[list] = mapped_column(JSON, default=list)
