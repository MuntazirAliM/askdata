
from __future__ import annotations

import csv
import datetime as dt
import decimal
import io
import os
import subprocess
import time
from dataclasses import dataclass, field

import config
from sqlguard import ALLOWED_RELATIONS, validate_sql

MAX_ROWS = 200


@dataclass
class QueryResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    sql: str = ""            # the SQL as the model wrote it
    ms: int = 0


def _conn_args() -> dict:
    return dict(host=config.get("PGHOST", "localhost"), port=config.get("PGPORT", "5432"),
                dbname=config.get("PGDATABASE", "shops"), password=config.get("AGENT_PASSWORD"))


def _clean(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


# ---------------------------------------------------------------- backends
def _run_psycopg(role: str, sql: str):
    import psycopg  # imported here so the project works without it
    args = _conn_args()
    with psycopg.connect(host=args["host"], port=args["port"], dbname=args["dbname"], user=role,
                         password=args["password"], autocommit=True, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d.name for d in cur.description]
            rows = [[_clean(v) for v in row] for row in cur.fetchall()]
    return columns, rows


def _run_psql(role: str, sql: str):
    from setup_db import find_psql
    args = _conn_args()
    env = os.environ.copy()
    env["PGPASSWORD"] = args["password"]
    cmd = [find_psql(), "-X", "-q", "--csv", "-v", "ON_ERROR_STOP=1", "-h", args["host"], "-p", str(args["port"]),
           "-d", args["dbname"], "-U", role, "-c", sql]
    out = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(_first_error_line(out.stderr))
    parsed = list(csv.reader(io.StringIO(out.stdout)))
    if not parsed:
        return [], []
    columns, rows = parsed[0], parsed[1:]
    # A one-column row holding NULL is printed as an empty line, which csv reads as no cells.
    rows = [row if row else [""] * len(columns) for row in rows]
    return columns, [[_coerce(v) for v in row] for row in rows]


def _coerce(text: str):
    """psql CSV gives text. Turn numbers back into numbers; leave everything else as text."""
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _first_error_line(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    for ln in lines:
        if ln.lower().startswith("error") or "error:" in ln.lower() or "fatal" in ln.lower():
            return ln
    return lines[0] if lines else "database error"


def _backend():
    if config.get("DB_BACKEND") == "psql":
        return _run_psql
    try:
        import psycopg  # noqa: F401
        return _run_psycopg
    except ImportError:
        return _run_psql


# ---------------------------------------------------------------- public API
def run_query(who: str, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
    """Validate, then run as the database role for `who`. Never raises; errors come back in the result."""
    verdict = validate_sql(sql, ALLOWED_RELATIONS, max_rows)
    if not verdict.ok:
        return QueryResult(False, error="Rejected before running: " + verdict.message, sql=sql)
    started = time.time()
    try:
        columns, rows = _backend()(config.db_role_for(who), verdict.sql)
    except Exception as exc:  # database errors are fed back to the model so it can fix its SQL
        message = " ".join(str(exc).split())
        return QueryResult(False, error=message[:400], sql=sql, ms=int((time.time() - started) * 1000))
    truncated = len(rows) > max_rows
    return QueryResult(True, columns, rows[:max_rows], truncated, sql=sql, ms=int((time.time() - started) * 1000))


SCHEMA_QUERY = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), coalesce(d.description, ''),
       coalesce(obj_description(c.oid, 'pg_class'), '')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = a.attnum
WHERE c.relname IN ('daily_sales', 'weekly_sales', 'product_sales', 'locations')
ORDER BY c.relname, a.attnum
"""

_schema_cache: dict[str, str] = {}


def get_schema_text(who: str) -> str:
    """Compact description of the allowed views, built from the database's own comments."""
    role = config.db_role_for(who)
    if role in _schema_cache:
        return _schema_cache[role]
    columns, rows = _backend()(role, SCHEMA_QUERY)
    by_table: dict[str, list] = {}
    notes: dict[str, str] = {}
    for table, column, ctype, comment, table_comment in rows:
        by_table.setdefault(table, []).append((column, ctype, comment))
        notes[table] = table_comment
    lines = []
    for table, cols in by_table.items():
        lines.append(f"{table}: {notes.get(table, '')}".rstrip(": "))
        for column, ctype, comment in cols:
            short = ctype.split("(")[0].replace("timestamp with time zone", "timestamptz").replace("character varying", "text")
            lines.append(f"  {column} {short}" + (f" - {comment}" if comment else ""))
    text = "\n".join(lines)
    _schema_cache[role] = text
    return text


def latest_data_date(who: str) -> str:
    result = run_query(who, "SELECT max(sales_date) FROM daily_sales")
    if result.ok and result.rows and result.rows[0][0]:
        return str(result.rows[0][0])
    return ""
