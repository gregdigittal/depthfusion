"""DepthFusion SQL migrations package.

Migration files are sequentially numbered SQL scripts applied by the
migration runner at store initialisation time.  Each file is idempotent
(uses IF NOT EXISTS / ADD COLUMN … IF NOT EXISTS semantics where supported,
or guards via _df_schema_migrations tracking table).

Numbering:
  0001_acl_columns.sql  — E-50 ACL: add acl_allow + classification columns
                          to all six SQLite-backed stores.
"""
