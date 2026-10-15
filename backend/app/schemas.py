from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

Text100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
Money = Annotated[int, Field(strict=True, gt=0, le=100_000_000)]
Frequency = Literal['weekly', 'monthly']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Register(Input):
    full_name: Text100
    phone: str = Field(pattern=r'^\+234[789][01]\d{8}$')
    university: Text100
    department: Text100
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class Login(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailInput(Input):
    email: EmailStr


class Verify(EmailInput):
    code: str = Field(pattern=r'^\d{6}$')


class Reset(Verify):
    password: str = Field(min_length=12, max_length=128)


class Profile(Input):
    full_name: Text100
    department: Text100


class Preferences(Input):
    circle: bool
    finance: bool
    chat: bool
    email: bool = True


class CircleInput(Input):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=10, max_length=2000)
    purpose: str = Field(min_length=3, max_length=80)
    university: Text100
    department: str = Field(default='', max_length=120)
    visibility: Literal['private', 'discoverable'] = 'discoverable'
    joining_method: Literal['open', 'approval', 'invitation'] = 'approval'
    contribution_frequency: Frequency = 'monthly'
    contribution_target: int = Field(default=0, strict=True, ge=0, le=100_000_000)
    membership_limit: int = Field(default=20, ge=2, le=100)
    invitations_enabled: bool = True
    rules: str = Field(min_length=10, max_length=4000)


class JoinInvitation(Input):
    token: str = Field(min_length=20, max_length=200)


class MemberAction(Input):
    action: Literal['approve', 'reject', 'remove', 'admin', 'member', 'transfer_ownership']


class GoalInput(Input):
    name: Text100
    description: str = Field(default='', max_length=2000)
    target_minor: Money
    start_date: datetime
    target_date: datetime
    schedule: Frequency = 'monthly'
    eligible_user_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('start_date', 'target_date')
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError('Include a timezone in dates.')
        return v


class Contribution(Input):
    amount_minor: Money
    goal_id: str | None = None


class MessageInput(Input):
    body: str = Field(min_length=1, max_length=4000)


class RequestInput(Input):
    principal_minor: Money
    purpose: str = Field(min_length=5, max_length=200)
    explanation: str = Field(min_length=10, max_length=4000)
    frequency: Frequency = 'monthly'
    instalment_count: int = Field(ge=1, le=24)


class ProposalInput(Input):
    request_id: str
    interest_bps: int = Field(default=0, ge=0, le=2000)
    fees_minor: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    instalment_count: int = Field(ge=1, le=24)
    frequency: Frequency = 'monthly'
    quorum_bps: int = Field(default=5000, ge=1000, le=10000)
    threshold_bps: int = Field(default=6000, ge=5001, le=10000)
    voting_hours: int = Field(default=24, ge=1, le=168)
    conditions: str = Field(default='', max_length=2000)


class VoteInput(Input):
    choice: Literal['support', 'reject']


class AcceptInput(Input):
    version: int = Field(ge=1)
    terms_hash: str = Field(min_length=64, max_length=64)


class Repayment(Input):
    amount_minor: Money
    # Simulation only: lets a demo exercise the failed-collection path honestly. No money moves either way.
    outcome: Literal['success', 'failure'] = 'success'


class Copilot(Input):
    question: str = Field(min_length=3, max_length=1500)
    loan_id: str | None = None
    compare_loan_id: str | None = None
    use_ai: bool = False
    consent: bool = False
