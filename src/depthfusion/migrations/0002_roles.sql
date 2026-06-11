-- Migration 0002: Add roles table to the principal_store DB
-- E-50 Authorization Model — T-556 (Role/capability schema)
--
-- Tracks role assignments: which principal holds which role,
-- who granted it, and when.  Roles map to capability sets defined in
-- depthfusion.authz.roles.ROLE_CAPABILITIES.
--
-- Canonical roles: owner | admin | member | viewer
--
-- Applied once per identity.db via the _df_schema_migrations tracking table.
-- Running this migration twice is safe — CREATE TABLE IF NOT EXISTS guards it.

CREATE TABLE IF NOT EXISTS roles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_id TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    granted_by   TEXT    NOT NULL,
    granted_at   REAL    NOT NULL,
    UNIQUE (principal_id, role)
);

CREATE INDEX IF NOT EXISTS idx_roles_principal_id ON roles(principal_id);
