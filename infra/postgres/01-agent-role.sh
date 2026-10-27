#!/bin/bash
# Runs once, when the Postgres volume is first initialised.
# Creates the read-only login the AI agent's SQL tool uses. Table-level SELECT
# grants are applied by the API on startup (after it creates the tables).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v agent_password="$AGENT_DB_PASSWORD" <<'SQL'
CREATE ROLE agent_ro LOGIN PASSWORD :'agent_password';
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '10s';
REVOKE ALL ON SCHEMA public FROM agent_ro;
SQL
