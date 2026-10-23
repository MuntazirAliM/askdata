
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_data import generate  # noqa: E402


def load_env() -> None:
    """Minimal .env reader so no extra package is needed."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("No .env file found. Copy .env.example to .env and fill in your passwords.")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def find_psql() -> str:
    explicit = os.environ.get("PSQL_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("psql")
    if found:
        return found
    patterns = [r"D:\PostgreSQL\*\bin\psql.exe", r"C:\Program Files\PostgreSQL\*\bin\psql.exe", "/usr/bin/psql"]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    sys.exit("Could not find psql. Add PSQL_PATH=D:\\PostgreSQL\\18\\bin\\psql.exe to your .env file.")


def run(psql: str, args: list[str], extra_env: dict | None = None) -> str:
    env = os.environ.copy()
    env.update(extra_env or {})
    cmd = [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", *args]
    result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stderr.strip())
        sys.exit(f"psql failed while running: {' '.join(args)[:90]}")
    return result.stdout


def main() -> None:
    load_env()
    for name in ("PGPASSWORD", "AGENT_PASSWORD"):
        if not os.environ.get(name) or "your_" in os.environ[name] or "choose_" in os.environ[name]:
            sys.exit(f"Set {name} in your .env file first.")
    psql = find_psql()
    print(f"Using {psql}")

    print("1/4 Creating tables and views ...")
    run(psql, ["-f", "db/01_schema.sql"])

    print("2/4 Generating fictional data ...")
    info = generate(ROOT / "data")
    print(f"    {info['orders']:,} orders, {info['items']:,} items, {info['start']} to {info['end']}")

    print("3/4 Loading data ...")
    cols = {
        "locations": "id,name,city,opened_on",
        "products": "id,name,category,price,unit_cost",
        "orders": "id,location_id,ordered_at,channel,discount_pct,status,total",
        "order_items": "id,order_id,location_id,product_id,quantity,unit_price",
        "app_users": "id,name,role,location_id,db_role",
    }
    args = ["-c", "SET timezone TO 'Europe/London'"]
    for table, column_list in cols.items():
        args += ["-c", rf"\copy {table} ({column_list}) FROM 'data/{table}.csv' WITH (FORMAT csv, HEADER true)"]
    run(psql, args)

    print("4/4 Creating roles and row-level security ...")
    run(psql, ["-v", f"agent_password={os.environ['AGENT_PASSWORD']}", "-f", "db/02_security.sql"])
    run(psql, ["-c", "ANALYZE"])

    summary = run(psql, ["-At", "-F", " | ", "-c",
                         "SELECT l.name, count(*), round(sum(o.total)) FROM orders o "
                         "JOIN locations l ON l.id = o.location_id WHERE o.status = 'completed' "
                         "GROUP BY l.name ORDER BY 2 DESC"])
    print("\nCompleted orders and revenue by location (GBP):")
    print(summary.rstrip())
    print("\nDone. Next: python app/check_security.py")


if __name__ == "__main__":
    main()
