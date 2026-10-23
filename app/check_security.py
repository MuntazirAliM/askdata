
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_db import find_psql, load_env  # noqa: E402

SHOW_LOCS = "SELECT string_agg(DISTINCT location_id::text, ',') FROM {}"


def run(psql: str, role: str, statements: list[str]):
    env = os.environ.copy()
    env["PGPASSWORD"] = env["AGENT_PASSWORD"]
    cmd = [psql, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1",
           "-h", env.get("PGHOST", "localhost"), "-p", env.get("PGPORT", "5432"),
           "-d", env.get("PGDATABASE", "shops"), "-U", role]
    for statement in statements:
        cmd += ["-c", statement]
    result = subprocess.run(cmd, env=env, text=True, capture_output=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip().lower()


def main() -> None:
    load_env()
    psql = find_psql()
    mgr = "askdata_mgr_3"  # Leeds manager

    tests = [
        ("Owner sees every location in daily_sales", "askdata_owner",
         ["SELECT (SELECT count(DISTINCT location_id) FROM daily_sales) = (SELECT count(*) FROM locations)"],
         lambda rc, out, err: rc == 0 and out == "t"),
        ("Leeds manager sees only Leeds in daily_sales", mgr,
         [SHOW_LOCS.format("daily_sales")], lambda rc, out, err: rc == 0 and out == "3"),
        ("Leeds manager sees no orders from other locations", mgr,
         ["SELECT count(*) FROM orders WHERE location_id <> 3"], lambda rc, out, err: rc == 0 and out == "0"),
        ("Leeds manager sees only Leeds order items", mgr,
         [SHOW_LOCS.format("order_items")], lambda rc, out, err: rc == 0 and out == "3"),
        ("Leeds manager sees only their own location row", mgr,
         ["SELECT count(*) FROM locations"], lambda rc, out, err: rc == 0 and out == "1"),
        ("Leeds manager cannot read the app_users table", mgr,
         ["SELECT * FROM app_users"], lambda rc, out, err: rc != 0 and "permission denied" in err),
        ("Leeds manager cannot delete data", mgr,
         ["DELETE FROM orders"], lambda rc, out, err: rc != 0),
        ("Owner cannot delete data either", "askdata_owner",
         ["DELETE FROM orders"], lambda rc, out, err: rc != 0),
        ("Prompt-injection style role switch gains nothing", mgr,
         ["SELECT set_config('role', 'askdata_owner', false)", SHOW_LOCS.format("orders")],
         lambda rc, out, err: rc != 0 or out == "3"),
        ("A slow query is cancelled by the 5 second limit", mgr,
         ["SELECT pg_sleep(8)"], lambda rc, out, err: rc != 0 and "timeout" in err),
    ]

    passed = 0
    print(f"{'Result':<7} Test")
    for name, role, statements, check in tests:
        ok = check(*run(psql, role, statements))
        passed += ok
        print(f"{'PASS' if ok else 'FAIL':<7} {name}")
    print(f"\n{passed} of {len(tests)} checks passed.")
    sys.exit(0 if passed == len(tests) else 1)


if __name__ == "__main__":
    main()
