
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """Load .env if it exists. Real environment variables win over the file."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    values = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")   # a later line overrides an earlier one
    for key, value in values.items():
        os.environ.setdefault(key, value)


load_env()

# Which person is asking decides which database login is used.
# "owner" sees every site. "manager:3" sees only location 3 (enforced by the database).
LOCATION_NAMES = {1: "Soho (London)", 2: "Northern Quarter (Manchester)", 3: "Headingley (Leeds)",
                  4: "Clifton (Bristol)", 5: "Merchant City (Glasgow)"}


def db_role_for(who: str) -> str:
    if who == "owner":
        return "askdata_owner"
    if who.startswith("manager:") and who.split(":", 1)[1].isdigit() and int(who.split(":", 1)[1]) in LOCATION_NAMES:
        return f"askdata_mgr_{int(who.split(':', 1)[1])}"
    raise ValueError(f"Unknown user {who!r}. Use 'owner' or 'manager:1' to 'manager:5'.")


def describe_user(who: str) -> str:
    if who == "owner":
        return "the owner of all five locations; data for every location is visible"
    loc = int(who.split(":", 1)[1])
    return (f"the manager of {LOCATION_NAMES[loc]}; the database only returns rows for that one location, "
            f"so do not filter by location and do not compare with other locations")


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
