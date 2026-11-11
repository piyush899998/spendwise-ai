"""Small, explicit schema migration runner.

Each numbered migration is applied once and recorded in ``schema_migrations``.
Keeping the runner explicit makes the initial SQLite migration testable now and
leaves a clear seam for future PostgreSQL/Alembic adoption.
"""
from __future__ import annotations

from sqlalchemy import Engine, text

from .models import Base


CURRENT_REVISION = 9

INITIAL_TABLES = {
    'users', 'payment_intents', 'transactions', 'classification_decisions',
    'split_expenses', 'participants', 'reimbursements', 'import_batches',
}
AUDIT_TABLES = {'classification_rules', 'transaction_events'}
FINANCE_TABLES = {
    'accounts', 'recurring_schedules', 'recurring_postings',
    'budgets', 'savings_goals', 'goal_contributions',
}
AI_TABLES = {'insight_feedback', 'chat_sessions', 'chat_messages', 'financial_reports', 'recurring_candidates'}
ACTION_TABLES = {'chat_action_previews'}
CONTACT_TABLES = {'person_contacts'}


def _ensure_migration_table(connection):
    connection.execute(text(
        'CREATE TABLE IF NOT EXISTS schema_migrations ('
        'revision INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)'
    ))


def _create_tables(connection, names):
    Base.metadata.create_all(
        connection,
        tables=[Base.metadata.tables[name] for name in names],
    )


def _migrate_transaction_reference_index(connection):
    """Replace the old global reference constraint with a user-scoped index."""
    if connection.dialect.name == 'sqlite':
        # SQLite cannot drop a table-level unique constraint. Rebuild only the
        # transactions table while preserving all existing rows and FKs.
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.exec_driver_sql('''
            CREATE TABLE transactions_new (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                intent_id VARCHAR(36),
                external_ref VARCHAR(128),
                recipient VARCHAR(200) NOT NULL,
                amount_paise INTEGER NOT NULL,
                direction VARCHAR(10) NOT NULL,
                status VARCHAR(20) NOT NULL,
                source VARCHAR(30) NOT NULL,
                category VARCHAR(100) NOT NULL,
                treatment VARCHAR(30) NOT NULL,
                confirmed BOOLEAN NOT NULL,
                needs_review BOOLEAN NOT NULL,
                occurred_at DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users (id),
                FOREIGN KEY(intent_id) REFERENCES payment_intents (id)
            )
        ''')
        connection.exec_driver_sql('''
            INSERT INTO transactions_new
            SELECT id, user_id, intent_id, external_ref, recipient, amount_paise,
                   direction, status, source, category, treatment, confirmed,
                   needs_review, occurred_at
            FROM transactions
        ''')
        connection.exec_driver_sql('DROP TABLE transactions')
        connection.exec_driver_sql('ALTER TABLE transactions_new RENAME TO transactions')
        connection.exec_driver_sql(
            'CREATE INDEX ix_transactions_user_id ON transactions (user_id)'
        )
        connection.exec_driver_sql(
            'CREATE UNIQUE INDEX uq_transactions_user_external_ref '
            'ON transactions (user_id, external_ref) WHERE external_ref IS NOT NULL'
        )
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        return

    connection.exec_driver_sql(
        'DROP INDEX IF EXISTS uq_transactions_external_ref'
    )
    connection.exec_driver_sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_user_external_ref '
        'ON transactions (user_id, external_ref) WHERE external_ref IS NOT NULL'
    )


def _migrate_transaction_account_fields(connection):
    """Add account/type metadata without rewriting historical transactions."""
    if connection.dialect.name == 'sqlite':
        columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(transactions)')}
        additions = [
            ('account_id', 'VARCHAR(36)'),
            ('transaction_type', 'VARCHAR(30)'),
            ('transfer_group_id', 'VARCHAR(36)'),
            ('note', 'TEXT'),
        ]
        for name, column_type in additions:
            if name not in columns:
                connection.exec_driver_sql(f'ALTER TABLE transactions ADD COLUMN {name} {column_type}')
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_transactions_account_id ON transactions (account_id)'
        )
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_transactions_transfer_group_id ON transactions (transfer_group_id)'
        )
        intent_columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(payment_intents)')}
        if 'account_id' not in intent_columns:
            connection.exec_driver_sql('ALTER TABLE payment_intents ADD COLUMN account_id VARCHAR(36)')
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_payment_intents_account_id ON payment_intents (account_id)'
        )
        return
    for name, column_type in [
        ('account_id', 'VARCHAR(36)'), ('transaction_type', 'VARCHAR(30)'),
        ('transfer_group_id', 'VARCHAR(36)'), ('note', 'TEXT'),
    ]:
        connection.exec_driver_sql(
            f'ALTER TABLE transactions ADD COLUMN IF NOT EXISTS {name} {column_type}'
        )
    connection.exec_driver_sql(
        'CREATE INDEX IF NOT EXISTS ix_transactions_account_id ON transactions (account_id)'
    )
    connection.exec_driver_sql(
        'CREATE INDEX IF NOT EXISTS ix_transactions_transfer_group_id ON transactions (transfer_group_id)'
    )
    connection.exec_driver_sql(
        'ALTER TABLE payment_intents ADD COLUMN IF NOT EXISTS account_id VARCHAR(36)'
    )
    connection.exec_driver_sql(
        'CREATE INDEX IF NOT EXISTS ix_payment_intents_account_id ON payment_intents (account_id)'
    )


def _migrate_imported_transaction_treatments(connection):
    """Repair historical CSV rows without deleting or recreating them.

    Older imports stored every row as ``unclassified`` and left
    ``transaction_type`` empty. Direction is sufficient to establish cash
    flow treatment, while classification decisions remain independently
    reviewable.
    """
    connection.execute(text("""
        UPDATE transactions
        SET transaction_type = CASE WHEN direction = 'credit' THEN 'income' ELSE 'expense' END,
            treatment = CASE WHEN direction = 'credit' THEN 'income' ELSE 'expense' END
        WHERE source = 'csv'
          AND (transaction_type IS NULL OR transaction_type = '')
          AND treatment = 'unclassified'
    """))


def _migrate_ai_schema(connection):
    """Add AI metadata tables and a nullable merchant projection in place."""
    _create_tables(connection, AI_TABLES)
    columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(transactions)')} if connection.dialect.name == 'sqlite' else set()
    if connection.dialect.name == 'sqlite':
        if 'merchant' not in columns:
            connection.exec_driver_sql('ALTER TABLE transactions ADD COLUMN merchant VARCHAR(200)')
    else:
        connection.exec_driver_sql('ALTER TABLE transactions ADD COLUMN IF NOT EXISTS merchant VARCHAR(200)')


def _migrate_person_contacts(connection):
    _create_tables(connection, CONTACT_TABLES)
    if connection.dialect.name == 'sqlite':
        columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(participants)')}
        if 'contact_id' not in columns:
            connection.exec_driver_sql('ALTER TABLE participants ADD COLUMN contact_id VARCHAR(36) REFERENCES person_contacts (id)')
        connection.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_participants_contact_id ON participants (contact_id)')
    else:
        connection.exec_driver_sql('ALTER TABLE participants ADD COLUMN IF NOT EXISTS contact_id VARCHAR(36) REFERENCES person_contacts (id)')
        connection.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_participants_contact_id ON participants (contact_id)')


def apply_migrations(engine: Engine):
    with engine.begin() as connection:
        _ensure_migration_table(connection)
        applied = {
            row[0] for row in connection.execute(
                text('SELECT revision FROM schema_migrations ORDER BY revision')
            )
        }
        if 1 not in applied:
            _create_tables(connection, INITIAL_TABLES)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 1},
            )
            applied.add(1)
        if 2 not in applied:
            _create_tables(connection, AUDIT_TABLES)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 2},
            )
            applied.add(2)
        if 3 not in applied:
            _migrate_transaction_reference_index(connection)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 3},
            )
            applied.add(3)
        if 4 not in applied:
            _create_tables(connection, FINANCE_TABLES)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 4},
            )
            applied.add(4)
        if 5 not in applied:
            _migrate_transaction_account_fields(connection)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 5},
            )
            applied.add(5)
        if 6 not in applied:
            _migrate_imported_transaction_treatments(connection)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 6},
            )
            applied.add(6)
        if 7 not in applied:
            _migrate_ai_schema(connection)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 7},
            )
            applied.add(7)
        if 8 not in applied:
            _create_tables(connection, ACTION_TABLES)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 8},
            )
            applied.add(8)
        if 9 not in applied:
            _migrate_person_contacts(connection)
            connection.execute(
                text('INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)'),
                {'revision': 9},
            )
            applied.add(9)


def migration_status(engine: Engine):
    with engine.connect() as connection:
        _ensure_migration_table(connection)
        revisions = [
            row[0] for row in connection.execute(
                text('SELECT revision FROM schema_migrations ORDER BY revision')
            )
        ]
    return {'current_revision': max(revisions, default=0), 'applied_revisions': revisions}
