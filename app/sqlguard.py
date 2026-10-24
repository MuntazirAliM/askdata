
from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOWED_RELATIONS = {"daily_sales", "weekly_sales", "product_sales", "locations"}
MAX_SQL_CHARS = 4000

FORBIDDEN_WORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "grant", "revoke", "truncate", "copy",
    "call", "do", "set", "reset", "execute", "prepare", "deallocate", "into", "vacuum", "analyze",
    "lock", "listen", "notify", "unlisten", "begin", "commit", "rollback", "savepoint", "declare",
    "fetch", "move", "refresh", "reindex", "cluster", "comment", "discard", "load", "import",
    "explain", "show", "recursive", "lateral", "only", "tablesample",
}

# Functions the agent may use. Anything else followed by "(" is rejected.
ALLOWED_FUNCTIONS = {
    "sum", "count", "avg", "min", "max", "round", "coalesce", "nullif", "greatest", "least", "abs",
    "ceil", "ceiling", "floor", "power", "sqrt", "ln", "log", "mod", "sign", "trunc",
    "date_trunc", "date_part", "extract", "to_char", "age", "make_date", "now",
    "lower", "upper", "length", "concat", "substring", "trim", "replace", "left", "right",
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value",
    "nth_value", "cume_dist", "percent_rank", "percentile_cont", "percentile_disc",
    "stddev", "stddev_samp", "stddev_pop", "variance", "var_samp", "var_pop", "corr",
    "string_agg", "array_agg", "bool_and", "bool_or", "cast", "date",
}
# Type names that can be followed by "(" in a cast, e.g. numeric(10,2).
TYPE_NAMES = {"numeric", "decimal", "varchar", "char", "character", "float", "real", "timestamp", "timestamptz", "time"}
# Words that legitimately sit in front of "(" without being a function call.
PAREN_KEYWORDS = {
    "in", "over", "filter", "within", "as", "on", "and", "or", "not", "exists", "from", "join", "where",
    "select", "by", "when", "then", "else", "case", "between", "any", "all", "using", "union",
    "intersect", "except", "having", "distinct", "limit", "offset", "like", "ilike", "is", "values",
    "partition", "order", "group", "end", "interval", "with", "rows", "range", "preceding", "following",
    "unbounded", "current", "row", "asc", "desc", "nulls", "first", "last", "similar", "to", "escape",
}
RESERVED_AFTER_RELATION = {
    "where", "group", "order", "join", "left", "right", "inner", "full", "cross", "natural", "outer",
    "on", "using", "limit", "offset", "union", "intersect", "except", "having", "window", "for",
    "select", "from",
}
FROM_IN_FUNCTION = {"extract", "substring", "trim", "overlay", "position"}


@dataclass
class Verdict:
    ok: bool
    message: str = ""            # reason when rejected
    sql: str = ""                # the SQL to run when accepted (wrapped with a row limit)
    relations: list[str] = field(default_factory=list)


def _tokenize(sql: str):
    """Return a list of (kind, text). Raises ValueError with a plain-English reason."""
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif sql.startswith("--", i) or sql.startswith("/*", i):
            raise ValueError("Comments are not allowed. Remove them.")
        elif c == "$":
            if re.match(r"\$\w*\$", sql[i:]) or (i + 1 < n and sql[i + 1].isdigit()):
                raise ValueError("Dollar quoting and numbered parameters are not allowed.")
            raise ValueError("The character $ is not allowed.")
        elif c == "'":
            if tokens and tokens[-1][0] == "word" and tokens[-1][1] in {"e", "u", "b", "x", "n"} and tokens[-1][2] + len(tokens[-1][1]) == i:
                raise ValueError("Prefixed string literals (E'..', U&'..') are not allowed.")
            j = i + 1
            while True:
                if j >= n:
                    raise ValueError("Unterminated string literal.")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            tokens.append(("str", sql[i:j + 1], i))
            i = j + 1
        elif c == '"':
            j = sql.find('"', i + 1)
            if j == -1:
                raise ValueError("Unterminated quoted identifier.")
            tokens.append(("word", sql[i + 1:j].lower(), i, True))
            i = j + 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            tokens.append(("word", sql[i:j].lower(), i, False))
            i = j
        elif c.isdigit():
            j = i
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            tokens.append(("num", sql[i:j], i))
            i = j
        elif c == ":" and sql.startswith("::", i):
            tokens.append(("punct", "::", i))
            i += 2
        elif c in "(),.;*+-/%<>=!|[]":
            tokens.append(("punct", c, i))
            i += 1
        else:
            raise ValueError(f"The character {c!r} is not allowed.")
    return tokens


def _is(tok, text):
    return tok is not None and tok[0] in ("word", "punct") and tok[1] == text


def validate_sql(sql: str, allowed: set[str] | None = None, max_rows: int = 200) -> Verdict:
    """Check one SQL string. If it passes, Verdict.sql is wrapped so at most max_rows+1 rows come back."""
    allowed = allowed or ALLOWED_RELATIONS
    sql = (sql or "").strip()
    if not sql:
        return Verdict(False, "The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        return Verdict(False, f"The query is too long (limit {MAX_SQL_CHARS} characters). Make it simpler.")
    try:
        tokens = _tokenize(sql)
    except ValueError as exc:
        return Verdict(False, str(exc))

    # One statement only; a single trailing semicolon is fine.
    if tokens and _is(tokens[-1], ";"):
        tokens = tokens[:-1]
        sql = sql.rstrip().rstrip(";").rstrip()
    if any(_is(t, ";") for t in tokens):
        return Verdict(False, "Only one statement is allowed. Remove the extra semicolon.")
    if not tokens:
        return Verdict(False, "The query is empty.")
    if tokens[0][0] != "word" or tokens[0][1] not in ("select", "with"):
        return Verdict(False, "Only SELECT queries are allowed. The query must start with SELECT or WITH.")

    # Parentheses must balance.
    depth = 0
    for t in tokens:
        if _is(t, "("):
            depth += 1
        elif _is(t, ")"):
            depth -= 1
            if depth < 0:
                return Verdict(False, "Unbalanced parentheses.")
    if depth != 0:
        return Verdict(False, "Unbalanced parentheses.")

    words = [t for t in tokens if t[0] == "word"]
    for t in words:
        if len(t) > 3 and t[3]:      # quoted identifiers are plain names, never keywords
            continue
        if t[1] in FORBIDDEN_WORDS:
            return Verdict(False, f"The keyword {t[1].upper()} is not allowed. This tool only reads data with SELECT.")
    for k, t in enumerate(tokens):
        if t[0] == "word" and t[1] == "for" and k + 1 < len(tokens) and tokens[k + 1][1] in {"update", "share", "no", "key"}:
            return Verdict(False, "Row locking (FOR UPDATE/SHARE) is not allowed.")

    # CTE names defined in this query: "WITH name AS (" or ", name AS (".
    ctes = set()
    for k in range(1, len(tokens) - 2):
        if tokens[k][0] == "word" and _is(tokens[k + 1], "as") and _is(tokens[k + 2], "(") and (
                _is(tokens[k - 1], "with") or _is(tokens[k - 1], ",")):
            ctes.add(tokens[k][1])

    # Functions: a word followed by "(" must be a known-safe function or a structural keyword.
    for k, t in enumerate(tokens[:-1]):
        if t[0] == "word" and _is(tokens[k + 1], "("):
            name = t[1]
            if k > 0 and _is(tokens[k - 1], "."):
                return Verdict(False, "Schema-qualified function calls are not allowed.")
            if name in PAREN_KEYWORDS or name in ALLOWED_FUNCTIONS or name in TYPE_NAMES or name in ctes:
                continue
            return Verdict(False, f"The function {name}() is not allowed. Use only standard aggregate, date and maths functions.")

    # Relations after FROM / JOIN must be on the allow-list (or a CTE defined in this query).
    paren_stack: list[str] = []
    relations: list[str] = []

    def skip_alias(j: int) -> int:
        if j < len(tokens) and _is(tokens[j], "as"):
            j += 1
        if j < len(tokens) and tokens[j][0] == "word" and tokens[j][1] not in RESERVED_AFTER_RELATION:
            j += 1
        return j

    def relation(j: int):
        """Parse one relation at j. Returns (next_index, error_or_None)."""
        if j >= len(tokens):
            return j, "A table name is missing after FROM or JOIN."
        if _is(tokens[j], "("):          # subquery: its own FROM clauses are checked by the main loop
            level, m = 0, j
            while m < len(tokens):
                if _is(tokens[m], "("):
                    level += 1
                elif _is(tokens[m], ")"):
                    level -= 1
                    if level == 0:
                        break
                m += 1
            return skip_alias(m + 1), None
        if tokens[j][0] != "word":
            return j, "A table name is expected after FROM or JOIN."
        name = tokens[j][1]
        nxt = j + 1
        if nxt < len(tokens) and _is(tokens[nxt], "."):
            if name != "public" or nxt + 1 >= len(tokens) or tokens[nxt + 1][0] != "word":
                return j, f"Only the allowed views can be used, not the schema {name}."
            name = tokens[nxt + 1][1]
            nxt += 2
        if nxt < len(tokens) and _is(tokens[nxt], "("):
            return j, f"Table functions such as {name}() are not allowed."
        if name not in allowed and name not in ctes:
            return j, (f"The table {name} is not available. Use only: {', '.join(sorted(allowed))}.")
        if name in allowed:
            relations.append(name)
        return skip_alias(nxt), None

    # The scan always moves one token at a time, so subqueries are walked into and their own
    # FROM clauses are checked. `after_from` holds the positions just after a FROM relation,
    # where a comma means another relation follows (FROM a, b).
    after_from: set[int] = set()
    k = 0
    while k < len(tokens):
        t = tokens[k]
        if k in after_from and _is(t, ","):
            j, err = relation(k + 1)
            if err:
                return Verdict(False, err)
            after_from.add(j)
        if _is(t, "("):
            opener = tokens[k - 1][1] if k > 0 and tokens[k - 1][0] == "word" else ""
            paren_stack.append(opener)
        elif _is(t, ")"):
            if paren_stack:
                paren_stack.pop()
        elif t[0] == "word" and not (len(t) > 3 and t[3]) and t[1] in ("from", "join"):
            if t[1] == "from" and ((k > 0 and _is(tokens[k - 1], "distinct")) or
                                   (paren_stack and paren_stack[-1] in FROM_IN_FUNCTION)):
                k += 1
                continue
            j, err = relation(k + 1)
            if err:
                return Verdict(False, err)
            if t[1] == "from":
                after_from.add(j)
        k += 1

    if not relations:
        return Verdict(False, "The query must read from one of: " + ", ".join(sorted(allowed)) + ".")

    wrapped = f"SELECT * FROM ({sql}\n) AS _agent_query LIMIT {int(max_rows) + 1}"
    return Verdict(True, "", wrapped, sorted(set(relations)))
