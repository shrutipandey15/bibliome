#!/usr/bin/env bash
#
# One-command dev start for the Bibliome API.
#
#   ./scripts/dev.sh
#
# Idempotent and safe to run every time. It repairs whatever is out of date and
# skips whatever is already fine, so a warm start is ~1s and a cold start (or a
# start after the repo has been moved) rebuilds the venv without you noticing.
#
# Flags:
#   --port N     listen on N instead of 8000
#   --rebuild    force a venv rebuild
#   --sql        echo every SQL statement (query debugging)
#   --no-reload  disable autoreload
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"
VENV="$ROOT/venv"
PY="$VENV/bin/python"
REQS="$ROOT/requirements-dev.txt"
STAMP="$VENV/.deps-stamp"

PORT=8000
RELOAD="--reload"
REBUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)      PORT="$2"; shift 2 ;;
    --rebuild)   REBUILD=1; shift ;;
    --sql)       export SQL_ECHO=1; shift ;;
    --no-reload) RELOAD=""; shift ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\033[36m▸\033[0m %s\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# 1. venv — rebuild if missing, or if its scripts point at a stale path.
# A moved repo leaves absolute shebangs dangling: python still works but every
# console script (uvicorn, pytest, alembic) dies with "required file not found".
if [[ $REBUILD -eq 1 ]] || [[ ! -x "$PY" ]] || ! "$VENV/bin/pytest" --version &>/dev/null; then
  step "Rebuilding venv (missing, stale, or moved)…"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r "$REQS"
  md5sum "$REQS" > "$STAMP"
  ok "venv rebuilt"
else
  # 2. deps — reinstall only when requirements-dev.txt actually changed.
  if ! md5sum -c --status "$STAMP" 2>/dev/null; then
    step "Dependencies changed — syncing…"
    "$PY" -m pip install -q -r "$REQS"
    md5sum "$REQS" > "$STAMP"
    ok "dependencies synced"
  fi
fi

# 3. Postgres must be up before alembic or the app will fail with a stack trace
# that says far less than this line does.
pg_isready -q 2>/dev/null || die "PostgreSQL is not accepting connections. Start it: sudo systemctl start postgresql"

# 4. Migrations — no-op when already at head.
if [[ "$("$VENV/bin/alembic" current 2>/dev/null | tail -1)" != *"(head)"* ]]; then
  step "Applying migrations…"
  "$VENV/bin/alembic" upgrade head
  ok "database at head"
fi

# 5. Free the port if a previous run is still holding it.
if lsof -ti "tcp:$PORT" &>/dev/null; then
  step "Port $PORT busy — stopping previous server…"
  lsof -ti "tcp:$PORT" | xargs -r kill
  sleep 1
fi

ok "http://127.0.0.1:$PORT/docs"
exec "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" $RELOAD
