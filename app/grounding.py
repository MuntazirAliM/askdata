
from __future__ import annotations

import re

DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(\s+\d{4})?\b", re.I)
NUMBER = re.compile(r"(?<![\w.])[£$€]?\d[\d,]*(?:\.\d+)?\s?(?:%|k\b|m\b|million|thousand)?", re.I)


def _to_float(token: str) -> tuple[float, bool]:
    """Return (value, is_marked). Marked numbers carry a currency or percent sign."""
    t = token.strip().lower()
    marked = t[:1] in "£$€" or t.endswith("%")
    mult = 1.0
    if re.search(r"(million|m)$", t):
        mult, t = 1e6, re.sub(r"(million|m)$", "", t)
    elif re.search(r"(thousand|k)$", t):
        mult, t = 1e3, re.sub(r"(thousand|k)$", "", t)
    marked = marked or mult != 1.0
    t = t.replace("£", "").replace("$", "").replace("€", "").replace("%", "").replace(",", "").strip()
    return float(t) * mult, marked


def numbers_in(text: str) -> list[tuple[str, float, bool]]:
    text = DATE.sub(" ", text)
    found = []
    for match in NUMBER.finditer(text):
        token = match.group(0)
        try:
            value, marked = _to_float(token)
        except ValueError:
            continue
        found.append((token.strip(), value, marked))
    return found


def _decimals(token: str) -> int:
    m = re.search(r"\.(\d+)", token)
    return len(m.group(1)) if m else 0


def _unit(token: str) -> float:
    t = token.lower().strip()
    if t.endswith("million") or re.search(r"\dm$", t):
        return 1e6
    if t.endswith("thousand") or re.search(r"\dk$", t):
        return 1e3
    return 1.0


def _supported(value: float, token: str, pool: list[float]) -> bool:
    """True if value matches a pool number after rounding to the precision the answer used."""
    d = _decimals(token)
    unit = _unit(token)
    tol = 0.5 * 10 ** (-d)
    value = abs(value)                       # "down 0.1" matches a result of -0.1
    for p in (abs(x) for x in pool):
        if unit != 1.0:                                   # "1.8 million" for 1,828,971
            if round(p / unit, d) == round(value / unit, d):
                return True
            continue
        if abs(p - value) <= max(tol, 1e-9) or round(p, d) == round(value, d):
            return True
        if abs(p * 100 - value) <= max(tol, 1e-9):        # 0.31 in the data, "31%" in the text
            return True
    return False


def ungrounded_numbers(answer: str, rows: list[list], question: str = "") -> list[str]:
    """Numbers in `answer` that appear in neither the result rows nor the question."""
    pool: list[float] = [float(len(rows))]
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, (int, float)):
                pool.append(float(cell))
            elif isinstance(cell, str):
                for _, value, _m in numbers_in(cell):
                    pool.append(value)
    pool += [value for _, value, _m in numbers_in(question)]
    missing = []
    for token, value, marked in numbers_in(answer):
        if not marked and value == int(value) and value <= 31:
            continue                      # small counts like "3 locations" or day numbers
        if not marked and 1990 <= value <= 2100:
            continue                      # years
        if not _supported(value, token, pool):
            missing.append(token)
    return missing
