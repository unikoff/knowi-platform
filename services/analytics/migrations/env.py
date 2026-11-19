"""Migrations run only through the explicit, locked migrator."""
from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("use the explicit Analytics migrator")
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
