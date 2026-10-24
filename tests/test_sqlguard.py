import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from sqlguard import validate_sql  # noqa: E402


class Accepts(unittest.TestCase):
    def ok(self, sql):
        v = validate_sql(sql)
        self.assertTrue(v.ok, f"{sql!r} rejected: {v.message}")
        return v

    def test_simple(self):
        v = self.ok("SELECT location_name, sum(revenue) FROM daily_sales GROUP BY 1 ORDER BY 2 DESC")
        self.assertIn("LIMIT 201", v.sql)
        self.assertEqual(v.relations, ["daily_sales"])

    def test_trailing_semicolon_and_case(self):
        self.ok("select 1 as x from Daily_Sales;")

    def test_with_cte_and_window(self):
        self.ok("""WITH weekly AS (SELECT date_trunc('week', sales_date)::date AS wk, location_city, sum(revenue) AS rev
                   FROM daily_sales GROUP BY 1, 2)
                   SELECT wk, location_city, rev, lag(rev) OVER (PARTITION BY location_city ORDER BY wk) AS prev FROM weekly""")

    def test_extract_from_is_not_a_table(self):
        self.ok("SELECT extract(dow FROM sales_date) AS dow, avg(revenue) FROM daily_sales GROUP BY 1")
        self.ok("SELECT substring(location_name FROM 1 FOR 3) FROM locations".replace(" FOR 3", ""))

    def test_is_distinct_from(self):
        self.ok("SELECT 1 FROM daily_sales WHERE refunds IS DISTINCT FROM 0")

    def test_joins_and_aliases(self):
        self.ok("SELECT d.location_city, p.category FROM daily_sales d JOIN product_sales p ON p.location_id = d.location_id AND p.sales_date = d.sales_date")
        self.ok("SELECT * FROM daily_sales AS d LEFT JOIN locations l ON l.id = d.location_id")

    def test_comma_join(self):
        self.ok("SELECT a.location_id FROM daily_sales a, locations b WHERE a.location_id = b.id")

    def test_subquery_in_from_and_where(self):
        self.ok("SELECT * FROM (SELECT location_id, sum(revenue) r FROM daily_sales GROUP BY 1) t WHERE r > (SELECT avg(revenue) FROM daily_sales)")

    def test_filter_and_percentile(self):
        self.ok("SELECT count(*) FILTER (WHERE revenue > 100), percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue) FROM daily_sales")

    def test_cast_with_precision(self):
        self.ok("SELECT round(sum(revenue)::numeric(12,2), 1), CAST(count(*) AS numeric(10,2)) FROM daily_sales")

    def test_weekly_view(self):
        self.ok("SELECT week_start, revenue FROM weekly_sales WHERE days_in_week = 7")

    def test_public_schema(self):
        self.ok("SELECT * FROM public.daily_sales")

    def test_string_with_keyword_inside(self):
        self.ok("SELECT * FROM locations WHERE name = 'drop table; --'")


class Rejects(unittest.TestCase):
    def bad(self, sql, fragment=""):
        v = validate_sql(sql)
        self.assertFalse(v.ok, f"{sql!r} was accepted")
        self.assertIn(fragment.lower(), v.message.lower(), v.message)

    def test_not_select(self):
        self.bad("DELETE FROM orders", "only select")
        self.bad("UPDATE locations SET name = 'x'", "only select")
        self.bad("DROP TABLE orders", "only select")
        self.bad("", "empty")

    def test_stacked_statements(self):
        self.bad("SELECT 1 FROM daily_sales; DROP TABLE orders", "one statement")
        self.bad("SELECT 1 FROM daily_sales;; ", "one statement")

    def test_data_modifying_cte(self):
        self.bad("WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM daily_sales", "delete")

    def test_select_into_and_locking(self):
        self.bad("SELECT * INTO newtable FROM daily_sales", "into")
        self.bad("SELECT * FROM daily_sales FOR UPDATE", "")

    def test_tables_outside_allow_list(self):
        self.bad("SELECT * FROM orders", "orders is not available")
        self.bad("SELECT * FROM app_users", "app_users")
        self.bad("SELECT * FROM daily_sales JOIN orders ON true", "orders")
        self.bad("SELECT * FROM daily_sales, order_items", "order_items")
        self.bad("SELECT * FROM pg_catalog.pg_roles", "schema")
        self.bad("SELECT * FROM information_schema.tables", "schema")
        self.bad("SELECT * FROM (SELECT * FROM orders) t", "orders")
        self.bad("SELECT * FROM (SELECT * FROM daily_sales) a, orders", "orders")
        self.bad("WITH x AS (SELECT * FROM orders) SELECT * FROM x", "orders")
        self.bad("SELECT * FROM daily_sales WHERE location_id IN (SELECT location_id FROM orders)", "orders")

    def test_dangerous_functions(self):
        self.bad("SELECT set_config('role', 'askdata_owner', false) FROM daily_sales", "set_config")
        self.bad("SELECT pg_sleep(10) FROM daily_sales", "pg_sleep")
        self.bad("SELECT current_setting('role') FROM daily_sales", "current_setting")
        self.bad("SELECT pg_catalog.pg_sleep(1) FROM daily_sales", "schema-qualified")
        self.bad("SELECT * FROM generate_series(1, 100000000)", "generate_series")
        self.bad("SELECT lo_import('/etc/passwd') FROM daily_sales", "lo_import")
        self.bad("SELECT dblink('x','y') FROM daily_sales", "dblink")

    def test_comments_and_quoting_tricks(self):
        self.bad("SELECT 1 FROM daily_sales -- hi", "comments")
        self.bad("SELECT 1 FROM daily_sales /* x */", "comments")
        self.bad("SELECT $$x$$ FROM daily_sales", "dollar")
        self.bad("SELECT E'\\x41' FROM daily_sales", "prefixed")
        self.bad("SELECT 'abc FROM daily_sales", "unterminated")

    def test_no_relation(self):
        self.bad("SELECT 1", "must read from")
        self.bad("SELECT now()", "must read from")

    def test_unbalanced(self):
        self.bad("SELECT (1 FROM daily_sales", "parentheses")

    def test_too_long(self):
        self.bad("SELECT " + "1," * 3000 + "1 FROM daily_sales", "too long")


if __name__ == "__main__":
    unittest.main()
