"""TEST ONLY: a Database stand-in that runs queries through the `psql` command line,
for machines without psycopg installed. Values are passed as psql variables and
quoted by psql itself (:'pN'). Don't use this in the app; use sitesignal.db.Database.
"""
from __future__ import annotations

import json
import re
import subprocess


class PsqlDatabase:
    def __init__(self, conn_args: list[str]):
        self.conn_args = conn_args  # e.g. ["-h", "/tmp", "-p", "5544", "-U", "postgres", "-d", "sitesignal"]

    def query(self, sql: str, params=()) -> list[dict]:
        params = list(params)
        args, i = [], 0

        def sub(_):
            nonlocal i
            v = params[i]
            i += 1
            if v is None:
                return "NULL"
            args.extend(["-v", f"p{i}={'true' if v is True else 'false' if v is False else v}"])
            return f":'p{i}'"

        body = re.sub(r"%s", sub, sql).strip().rstrip(";")
        returns_rows = body.lstrip().upper().startswith(("SELECT", "WITH")) or " RETURNING " in body.upper()
        if returns_rows:
            body = f"WITH __q AS ({body}) SELECT coalesce(json_agg(__q), '[]'::json) FROM __q"
        out = subprocess.run(["psql", *self.conn_args, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", *args],
                             input=body + ";\n", capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip())
        return json.loads(out.stdout.strip() or "[]") if returns_rows else []
