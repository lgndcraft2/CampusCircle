"""Instalments, repayment attempts, email deliveries, provider accounts, webhook events, AI interaction metadata."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

TABLES = ('email_deliveries', 'instalments', 'repayment_attempts', 'provider_accounts', 'webhook_events', 'ai_interactions')
APPEND_ONLY = ('repayment_attempts',)


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables[name] for name in TABLES])
    for table in APPEND_ONLY:
        if bind.dialect.name == 'sqlite':
            for action in ('UPDATE', 'DELETE'):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")
        elif bind.dialect.name == 'postgresql':
            op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION cc_append_only()')


def downgrade():
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables[name] for name in reversed(TABLES)])
