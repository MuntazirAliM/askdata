

DROP VIEW IF EXISTS product_sales, daily_sales;
DROP TABLE IF EXISTS order_items, orders, app_users, products, locations CASCADE;

CREATE TABLE locations (
    id        integer PRIMARY KEY,
    name      text NOT NULL,
    city      text NOT NULL,
    opened_on date NOT NULL
);

CREATE TABLE products (
    id        integer PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    category  text NOT NULL,
    price     numeric(8,2) NOT NULL,
    unit_cost numeric(8,2) NOT NULL
);

CREATE TABLE orders (
    id           bigint PRIMARY KEY,
    location_id  integer NOT NULL REFERENCES locations(id),
    ordered_at   timestamptz NOT NULL,
    channel      text NOT NULL CHECK (channel IN ('dine_in', 'takeaway', 'delivery')),
    discount_pct numeric(5,2) NOT NULL DEFAULT 0,
    status       text NOT NULL CHECK (status IN ('completed', 'refunded')),
    total        numeric(10,2) NOT NULL
);

CREATE TABLE order_items (
    id          bigint PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id),
    -- Copied from the order so row-level security can filter this table without a join.
    location_id integer NOT NULL REFERENCES locations(id),
    product_id  integer NOT NULL REFERENCES products(id),
    quantity    integer NOT NULL CHECK (quantity > 0),
    unit_price  numeric(8,2) NOT NULL
);

-- Who can log in to the app, and which database role they connect as.
CREATE TABLE app_users (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    role        text NOT NULL CHECK (role IN ('owner', 'manager')),
    location_id integer REFERENCES locations(id),
    db_role     text NOT NULL
);

CREATE INDEX orders_location_time_idx ON orders (location_id, ordered_at);
CREATE INDEX order_items_order_idx    ON order_items (order_id);
CREATE INDEX order_items_location_idx ON order_items (location_id, product_id);

-- ---------------------------------------------------------------------------
-- Reporting views. The AI agent queries ONLY these (plus lookup tables).
-- security_invoker = true makes row-level security apply to whoever runs the query.
-- ---------------------------------------------------------------------------

CREATE VIEW daily_sales WITH (security_invoker = true) AS
SELECT
    o.location_id,
    l.name AS location_name,
    (o.ordered_at AT TIME ZONE 'Europe/London')::date AS sales_date,
    count(*) FILTER (WHERE o.status = 'completed') AS orders,
    coalesce(sum(o.total) FILTER (WHERE o.status = 'completed'), 0)::numeric(12,2) AS revenue,
    round(avg(o.total) FILTER (WHERE o.status = 'completed'), 2) AS avg_order_value,
    count(*) FILTER (WHERE o.status = 'refunded') AS refunds,
    coalesce(sum(o.total) FILTER (WHERE o.status = 'refunded'), 0)::numeric(12,2) AS refunded_value,
    count(*) FILTER (WHERE o.status = 'completed' AND o.channel = 'dine_in')  AS dine_in_orders,
    count(*) FILTER (WHERE o.status = 'completed' AND o.channel = 'takeaway') AS takeaway_orders,
    count(*) FILTER (WHERE o.status = 'completed' AND o.channel = 'delivery') AS delivery_orders,
    count(*) FILTER (WHERE o.status = 'completed' AND o.discount_pct > 0)     AS discounted_orders,
    round(avg(o.discount_pct) FILTER (WHERE o.status = 'completed'), 2)       AS avg_discount_pct
FROM orders o
JOIN locations l ON l.id = o.location_id
GROUP BY o.location_id, l.name, (o.ordered_at AT TIME ZONE 'Europe/London')::date;

CREATE VIEW product_sales WITH (security_invoker = true) AS
SELECT
    o.location_id,
    l.name AS location_name,
    (o.ordered_at AT TIME ZONE 'Europe/London')::date AS sales_date,
    p.name AS product_name,
    p.category,
    sum(oi.quantity) AS units,
    sum(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100))::numeric(12,2) AS revenue,
    sum(oi.quantity * (oi.unit_price * (1 - o.discount_pct / 100) - p.unit_cost))::numeric(12,2) AS gross_margin
FROM order_items oi
JOIN orders o    ON o.id = oi.order_id
JOIN products p  ON p.id = oi.product_id
JOIN locations l ON l.id = o.location_id
WHERE o.status = 'completed'
GROUP BY o.location_id, l.name, (o.ordered_at AT TIME ZONE 'Europe/London')::date, p.name, p.category;

-- Definitions the agent will read, so every number has a stated meaning.
COMMENT ON VIEW daily_sales   IS 'One row per location per day. All money in GBP.';
COMMENT ON VIEW product_sales IS 'One row per location, day and product, completed orders only. All money in GBP.';

COMMENT ON COLUMN daily_sales.orders          IS 'Count of completed orders (refunded orders excluded).';
COMMENT ON COLUMN daily_sales.revenue         IS 'Sum of order totals for completed orders, after discounts.';
COMMENT ON COLUMN daily_sales.avg_order_value IS 'Average total of completed orders.';
COMMENT ON COLUMN daily_sales.refunds         IS 'Count of refunded orders.';
COMMENT ON COLUMN daily_sales.refunded_value  IS 'Sum of totals of refunded orders.';
COMMENT ON COLUMN daily_sales.discounted_orders IS 'Completed orders that had any discount applied.';
COMMENT ON COLUMN daily_sales.avg_discount_pct  IS 'Average discount percentage across completed orders (0 to 100).';
COMMENT ON COLUMN product_sales.units         IS 'Total quantity sold.';
COMMENT ON COLUMN product_sales.revenue       IS 'Line revenue after the order discount.';
COMMENT ON COLUMN product_sales.gross_margin  IS 'Line revenue minus unit cost times quantity.';
