
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import config
import dbtools
from grounding import ungrounded_numbers
from llm import LLM

MAX_ATTEMPTS = 3
ROWS_SHOWN_TO_MODEL = 60

SQL_SYSTEM = """You are a careful data analyst for a restaurant group with five UK locations. You answer a question by writing ONE PostgreSQL SELECT query.

You are talking to {user}.
The data runs up to {latest}. Treat {latest} as "today" when the question says last week, last month, recently and so on.

Tables you may use (nothing else exists):
{schema}

Rules:
- Reply with one query inside a ```sql block and nothing else.
- Use only the columns listed above. All money is GBP.
- daily_sales has one row per location per day. weekly_sales has one row per location per week and already holds shares and rates: use it to compare weeks or find an unusual week. Use product_sales only for product or category questions.
- A site can be named by its location_name (Soho, Northern Quarter, Headingley, Clifton, Merchant City) or by its city (London, Manchester, Leeds, Bristol, Glasgow). These are different columns, so match both: (location_name = 'Soho' OR location_city = 'Soho').
- Do the arithmetic in SQL (shares, differences, percentage change, averages) so the result already holds the final numbers. Round money to 2 decimals and percentages to 1 decimal.
- A share such as delivery share is delivery_orders::numeric / orders, shown as a percentage.
- Refund rate is refunds / (orders + refunds), shown as a percentage.
- Use date ranges relative to {latest}, for example sales_date > DATE '{latest}' - 7 for the last 7 days.
- Average order value over several days is sum(revenue) / sum(orders), never an average of daily averages.
- "Recently" means the last 30 days. Never LIMIT 1 on a question that also asks how something changed; return every location.
- Never guess the dates of an event the user names (a dip, a discount week, a spike). Find it in the data with weekly_sales and show the numbers per week so the unusual week stands out.
- For questions about a past period, show at least 10 weeks of weekly_sales so the unusual week can be compared with normal ones. Include days_in_week and, when you compare weeks, leave out weeks where days_in_week < 7.
- A "why" question cannot be answered with a cause, but it CAN be answered with the numbers: show the weekly trend for that location, with revenue, orders, refund_rate_pct, discounted_share_pct and delivery_share_pct, and let the answer describe when and how much it changed. Use NO_SQL only when the data has nothing relevant.
- If you join a table, give it an alias and use that alias's own columns. Give every result column a unique alias.
- No comments, no semicolons inside the query.
- If the question cannot be answered from these tables (forecasts, staff, anything not listed), reply exactly: NO_SQL: <one short reason>

Example question: Which location had the highest revenue last week?
```sql
SELECT location_city, round(sum(revenue), 2) AS revenue
FROM daily_sales
WHERE sales_date > DATE '{latest}' - 7
GROUP BY location_city
ORDER BY revenue DESC
LIMIT 1
```

Example question: How did the delivery share change in Glasgow, comparing the last 30 days with the 30 days before?
```sql
SELECT CASE WHEN sales_date > DATE '{latest}' - 30 THEN 'last 30 days' ELSE 'previous 30 days' END AS period,
       round(100.0 * sum(delivery_orders) / sum(orders), 1) AS delivery_share_pct
FROM daily_sales
WHERE location_city = 'Glasgow' AND sales_date > DATE '{latest}' - 60
GROUP BY 1
ORDER BY 1
```"""

ANSWER_SYSTEM = """You explain database results to {user}.
Rules:
- Use ONLY numbers that appear in the result table. Never calculate, estimate or invent a number.
- Write one to four plain sentences. Use £ for money. No markdown, no bullet points.
- Say which period or location the numbers cover when the question or SQL makes that clear.
- If the result has no rows, say no matching data was found.
- A manager can see only their own location. If the question names or compares any other location, say clearly at the start of the answer that you can only see data for their own location, then answer for that location only.
- If the note says the result was cut short, say that only the first rows are shown.
- Describe what the numbers show. Never say or suggest what caused a change unless a column in the result measures that cause directly. Do not write "likely" or "probably".
- Describe direction in words (rose, fell, up, down) and quote the size without a minus sign.
- Only claim something is the highest, lowest or "any other" if you can see every relevant row in the result.
- Do not mention SQL, tables or the database."""


@dataclass
class Step:
    name: str
    detail: str


@dataclass
class AgentResult:
    question: str
    answer: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0
    ok: bool = False


def extract_sql(reply: str) -> tuple[str | None, str | None]:
    """Return (sql, no_sql_reason). Exactly one of them is set, or both are None if nothing usable."""
    reply = (reply or "").strip()
    if reply.upper().startswith("NO_SQL"):
        reason = reply.split(":", 1)[1].strip() if ":" in reply else "that is not in the data"
        return None, (reason.splitlines() or ["that is not in the data"])[0]
    block = re.search(r"```(?:sql|postgresql|postgres)?\s*\n?(.*?)```", reply, re.I | re.S)
    if block and block.group(1).strip():
        return block.group(1).strip(), None
    if re.match(r"^\s*(select|with)\b", reply, re.I):
        return reply, None
    return None, None


def _format_rows(columns: list[str], rows: list[list], limit: int = ROWS_SHOWN_TO_MODEL) -> str:
    def cell(v):
        if isinstance(v, float):
            return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
        return "" if v is None else str(v)
    lines = [" | ".join(columns)]
    lines += [" | ".join(cell(v) for v in row) for row in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows not shown")
    return "\n".join(lines)


def ask(question: str, who: str = "owner", llm: LLM | None = None, history: list[tuple[str, str]] | None = None,
        on_step=None) -> AgentResult:
    """Answer one question. `history` is a list of earlier (question, sql) pairs for follow-ups."""
    llm = llm or LLM()
    started = time.time()
    result = AgentResult(question=question, model=llm.label)

    def note(name: str, detail: str = "") -> None:
        result.steps.append(Step(name, detail))
        if on_step:
            on_step(name, detail)

    user_desc = config.describe_user(who)
    schema = dbtools.get_schema_text(who)
    latest = dbtools.latest_data_date(who) or time.strftime("%Y-%m-%d")
    system = SQL_SYSTEM.format(user=user_desc, latest=latest, schema=schema)

    prompt = question.strip()
    if history:
        earlier = "\n".join(f"Earlier question: {q}\nEarlier SQL: {s}" for q, s in history[-2:])
        prompt = f"{earlier}\n\nNew question (it may refer to the earlier one): {question.strip()}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

        # ---- 1-4: write, check, run, repair
    query = None
    fallback = None            # a query that ran but returned blank (NULL) values, kept in case the retry is no better
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result.attempts = attempt
        note("write", f"attempt {attempt}")
        reply = llm.chat(messages)
        sql, no_sql = extract_sql(reply)
        if no_sql:
            note("no_sql", no_sql)
            result.answer = f"I can't answer that from the sales data: {no_sql}"
            result.ok = True
            result.seconds = round(time.time() - started, 1)
            return result
        if not sql:
            problem = "I could not find a SQL query in your reply. Reply with only one ```sql block."
        else:
            note("sql", sql)
            query = dbtools.run_query(who, sql)
            dupes = sorted({c for c in query.columns if query.columns.count(c) > 1}) if query.ok else []
            blanks = [c for i, c in enumerate(query.columns) if any(r[i] is None for r in query.rows)] if query.ok else []
            empty = query.ok and not query.rows
            if query.ok and not dupes and (blanks or empty) and fallback is None and attempt < MAX_ATTEMPTS:
                fallback = query
                if empty:
                    problem = ("The query returned no rows. Check the filters first: a site can be named by location_name (Soho) "
                               "or by location_city (London), so match both with (location_name = 'X' OR location_city = 'X'). "
                               "If the question really is about something that does not exist or that the user cannot see, "
                               "return the same query.")
                    note("empty_check", "no rows")
                else:
                    problem = ("The result has blank (NULL) values in: " + ", ".join(blanks) + ". If that is not expected, fix the query. "
                               "Do not use COALESCE to turn blanks into 0 unless 0 is truly the answer: check the filters first, "
                               "for example a site name that matches no rows. Another cause is LAG() or another window function "
                               "applied after the rows were already filtered. If blanks are genuinely expected, return the same query.")
                    note("null_check", ", ".join(blanks))
            elif query.ok and not dupes:
                note("run", f"{len(query.rows)} rows in {query.ms} ms")
                break
            else:
                problem = query.error or ("Two result columns share the name " + ", ".join(dupes) +
                                          ". Give every column its own alias.")
                note("rejected" if problem.startswith("Rejected") else "db_error", problem)
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"That did not work: {problem}\nWrite a corrected query. Reply with only one ```sql block."}]
    else:
        if fallback is None:
            result.sql = (query.sql if query else "") or ""
            result.answer = "I could not produce a valid query for that question. Try rephrasing it."
            result.warnings.append("No valid query after 3 attempts.")
            result.seconds = round(time.time() - started, 1)
            return result
        query = fallback
        note("run", f"{len(query.rows)} rows (kept the first query)")
        
        result.sql, result.columns, result.rows, result.truncated = query.sql, query.columns, query.rows, query.truncated

    # ---- 5-6: explain, then verify numbers
    table = _format_rows(query.columns, query.rows)
    cut = f"\nNote: the result was cut short at {dbtools.MAX_ROWS} rows." if query.truncated else ""
    a_messages = [
        {"role": "system", "content": ANSWER_SYSTEM.format(user=user_desc)},
        {"role": "user", "content": f"Question: {question}\n\nSQL used:\n{query.sql}\n\nResult:\n{table}{cut}\n\nWrite the answer."},
    ]
    note("explain")
    answer = llm.chat(a_messages, max_tokens=400).strip()
    missing = ungrounded_numbers(answer, query.rows, question)
    if missing:
        note("verify_failed", ", ".join(missing))
        a_messages += [{"role": "assistant", "content": answer},
                       {"role": "user", "content": "These numbers are not in the result table: " + ", ".join(missing) +
                        ". Rewrite the answer using only numbers that appear in the table."}]
        answer = llm.chat(a_messages, max_tokens=400).strip()
        missing = ungrounded_numbers(answer, query.rows, question)
    if missing:
        result.warnings.append("These figures could not be matched to the data: " + ", ".join(missing))
        note("verify_warning", ", ".join(missing))
    else:
        note("verified", "every figure in the answer appears in the result")
    
    if who.startswith("manager:") and (not query.rows or all(c is None for row in query.rows for c in row)):
        answer += (" You are signed in as the manager of " + config.LOCATION_NAMES[int(who.split(":")[1])] +
                   ", so you can only see data for that location.")
    result.answer = answer
    result.ok = True
    result.seconds = round(time.time() - started, 1)
    return result
