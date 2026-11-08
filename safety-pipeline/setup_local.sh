#!/usr/bin/env bash
# One-step local setup: Postgres + the demo data (sites, assets, work orders, weather,
# Safety Index scores and decisions), restored from out/sitesignal_dump.sql.
#
#   ./setup_local.sh            uses Docker if it is installed, otherwise your local Postgres
#   DATABASE_URL=... ./setup_local.sh    restore into a database you already have
set -euo pipefail
cd "$(dirname "$0")"
DUMP=out/sitesignal_dump.sql
PSQL=(psql "${DATABASE_URL:-}")          # replaced below when Postgres runs in Docker

if [ -z "${DATABASE_URL:-}" ]; then
  if command -v docker >/dev/null 2>&1; then
    echo "==> Starting Postgres 16 in Docker (container: sitesignal-pg, port 5432)"
    if ! docker ps -a --format '{{.Names}}' | grep -qx sitesignal-pg; then
      docker run -d --name sitesignal-pg -e POSTGRES_PASSWORD=sitesignal -e POSTGRES_DB=sitesignal \
        -p 5432:5432 postgres:16 >/dev/null
    else
      docker start sitesignal-pg >/dev/null
    fi
    until docker exec sitesignal-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
    export DATABASE_URL="postgres://postgres:sitesignal@localhost:5432/sitesignal"
    PSQL=(docker exec -i sitesignal-pg psql -U postgres -d sitesignal)   # no psql needed on your machine
  elif command -v createdb >/dev/null 2>&1; then
    echo "==> Using your local Postgres (database: sitesignal)"
    createdb sitesignal 2>/dev/null || echo "    database 'sitesignal' already exists, reusing it"
    export DATABASE_URL="postgres://localhost/sitesignal"
    PSQL=(psql "$DATABASE_URL")
  else
    echo "Install Postgres first: 'brew install postgresql@16 && brew services start postgresql@16' (Mac)"
    echo "or Docker Desktop, then rerun this script."
    exit 1
  fi
fi

echo "==> Restoring demo data"
if "${PSQL[@]}" -tAc "select to_regclass('public.sites')" | grep -q sites; then
  echo "    tables already exist; skipping restore (drop the database to start fresh)"
else
  "${PSQL[@]}" -q -v ON_ERROR_STOP=1 < "$DUMP" >/dev/null
fi

echo "==> Python dependencies"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "==> Check"
"${PSQL[@]}" -c "select asset_id, safety_index, band, confidence from asset_worklist limit 5"

echo
echo "Done. Use this in your shell:"
echo "  export DATABASE_URL=\"$DATABASE_URL\""
echo "  source .venv/bin/activate"
