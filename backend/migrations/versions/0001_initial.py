"""Initial CampusCircle schema and append-only database protections."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


TABLES = ('users', 'browser_sessions', 'verifications', 'rate_buckets', 'circles', 'memberships', 'invitations', 'goals', 'messages',
          'notifications', 'loan_requests', 'loans', 'votes', 'journals', 'ledger_entries', 'audit_events', 'reminders', 'operations')


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables[name] for name in TABLES])
    immutable = ('journals', 'ledger_entries', 'audit_events', 'votes')
    if bind.dialect.name == 'sqlite':
        for table in immutable:
            for action in ('UPDATE', 'DELETE'):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")
        fields = [c.name for c in Base.metadata.tables['loans'].columns if c.name not in ('status', 'accepted_at', 'accepted_version')]
        condition = ' OR '.join(f'NEW.{f} IS NOT OLD.{f}' for f in fields)
        op.execute(f"CREATE TRIGGER loan_terms_immutable BEFORE UPDATE ON loans WHEN {condition} BEGIN SELECT RAISE(ABORT, 'immutable loan terms'); END")
    elif bind.dialect.name == 'postgresql':
        op.execute("CREATE FUNCTION cc_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only record'; END $$")
        for table in immutable:
            op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION cc_append_only()')
        op.execute("""CREATE FUNCTION cc_loan_terms() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF (to_jsonb(NEW) - ARRAY['status','accepted_at','accepted_version']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['status','accepted_at','accepted_version']) THEN RAISE EXCEPTION 'immutable loan terms'; END IF;
          RETURN NEW; END $$""")
        op.execute('CREATE TRIGGER loan_terms_immutable BEFORE UPDATE ON loans FOR EACH ROW EXECUTE FUNCTION cc_loan_terms()')
        op.execute("""CREATE FUNCTION cc_balanced_journal() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE jid text; net bigint; n bigint; BEGIN
          IF TG_TABLE_NAME = 'journals' THEN jid := NEW.id; ELSE jid := NEW.journal_id; END IF;
          SELECT COALESCE(SUM(amount_minor),0),COUNT(*) INTO net,n FROM ledger_entries WHERE journal_id=jid;
          IF net <> 0 OR n < 2 THEN RAISE EXCEPTION 'unbalanced journal'; END IF;
          RETURN NEW; END $$""")
        for table in ('journals', 'ledger_entries'):
            op.execute(f'CREATE CONSTRAINT TRIGGER {table}_balanced AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cc_balanced_journal()')


def downgrade():
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES])
    if op.get_bind().dialect.name == 'postgresql':
        for function in ('cc_append_only', 'cc_loan_terms', 'cc_balanced_journal'):
            op.execute(f'DROP FUNCTION IF EXISTS {function}()')
