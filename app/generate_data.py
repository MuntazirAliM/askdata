
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

LOCATIONS = [
    (1, "Soho", "London", dt.date(2019, 3, 1)),
    (2, "Northern Quarter", "Manchester", dt.date(2020, 9, 15)),
    (3, "Headingley", "Leeds", dt.date(2021, 4, 1)),
    (4, "Clifton", "Bristol", dt.date(2022, 6, 20)),
    (5, "Merchant City", "Glasgow", dt.date(2023, 2, 10)),
]
BASE_DAILY_ORDERS = {1: 150, 2: 120, 3: 95, 4: 85, 5: 80}
MANAGERS = {1: "Priya Nair", 2: "Tom Hargreaves", 3: "Chloe Bennett", 4: "Sam Okafor", 5: "Fiona MacLeod"}

DAY_OF_WEEK = [0.75, 0.80, 0.90, 1.00, 1.30, 1.45, 1.10]  # Monday to Sunday
MONTH = {1: .90, 2: .92, 3: 1.0, 4: 1.0, 5: 1.05, 6: 1.08, 7: 1.10, 8: 1.10, 9: 1.02, 10: 1.0, 11: 1.05, 12: 1.20}

# id, name, category, price, unit cost, popularity weight
PRODUCTS = [
    (1, "Soup of the day", "starter", 6.50, 1.60, 3),
    (2, "Garlic flatbread", "starter", 5.50, 1.10, 3),
    (3, "Crispy calamari", "starter", 8.50, 2.60, 2),
    (4, "Burrata and tomato", "starter", 9.50, 3.40, 2),
    (5, "Grilled chicken", "main", 15.50, 4.80, 4),
    (6, "Beef burger", "main", 14.50, 4.40, 5),
    (7, "Mushroom risotto", "main", 13.50, 3.30, 3),
    (8, "Seared salmon", "main", 18.00, 6.40, 3),
    (9, "Sirloin steak", "main", 24.00, 9.50, 2),
    (10, "Veggie curry", "main", 13.00, 2.90, 3),
    (11, "Fries", "side", 4.50, 0.80, 6),
    (12, "Seasonal greens", "side", 4.50, 1.00, 3),
    (13, "Side salad", "side", 4.50, 0.90, 2),
    (14, "Sticky toffee pudding", "dessert", 7.00, 1.70, 2),
    (15, "Chocolate fondant", "dessert", 7.50, 2.00, 2),
    (16, "Cheesecake", "dessert", 7.00, 1.90, 2),
    (17, "House lager", "drink", 5.50, 1.50, 5),
    (18, "House red (glass)", "drink", 7.00, 2.30, 3),
    (19, "Soft drink", "drink", 3.00, 0.50, 4),
    (20, "Flat white", "drink", 3.50, 0.60, 3),
]

HOURS = np.arange(11, 23)
HOUR_WEIGHTS = np.array([2, 6, 8, 5, 2, 2, 4, 8, 10, 9, 5, 2], dtype=float)
HOUR_WEIGHTS /= HOUR_WEIGHTS.sum()

CHANNELS = ["dine_in", "takeaway", "delivery"]
MEAN_ITEMS = {"dine_in": 3.2, "takeaway": 2.4, "delivery": 2.6}
BASE_REFUND_RATE = 0.015


def generate(out_dir: Path, end: dt.date | None = None, days: int = 365, seed: int = 42) -> dict:
    """Write CSV files into out_dir and return a summary dictionary."""
    rng = np.random.default_rng(seed)
    end = end or (dt.date.today() - dt.timedelta(days=1))
    out_dir.mkdir(parents=True, exist_ok=True)

    dates = [end - dt.timedelta(days=days - 1 - i) for i in range(days)]
    loc_ids = [loc[0] for loc in LOCATIONS]

    # Story windows, counted in days back from the last day of data.
    leeds_dip = (19, 25)        # Leeds trades about 30% lower for a week
    manc_discount = (39, 45)    # Manchester runs a 20% discount week
    glasgow_days = 90           # Glasgow delivery share climbs over the last 90 days
    bristol_refund_k = 12       # Bristol has a refund spike on one day

    day_rows = []
    for loc in loc_ids:
        for i, d in enumerate(dates):
            k = (end - d).days
            lam = BASE_DAILY_ORDERS[loc] * DAY_OF_WEEK[d.weekday()] * MONTH[d.month] * (1 + 0.0004 * i)
            p_delivery = {1: 0.22, 2: 0.18, 3: 0.17, 4: 0.20, 5: 0.20}[loc]
            p_takeaway = {1: 0.22, 2: 0.26, 3: 0.28, 4: 0.25, 5: 0.25}[loc]
            refund_p = BASE_REFUND_RATE
            promo = False

            if loc == 3 and leeds_dip[0] <= k <= leeds_dip[1]:
                lam *= 0.65
            if loc == 2 and manc_discount[0] <= k <= manc_discount[1]:
                lam *= 1.25
                promo = True
            if loc == 5 and k < glasgow_days:
                p_delivery = 0.20 + 0.22 * (1 - k / glasgow_days)
            if loc == 4 and k == bristol_refund_k:
                refund_p = 0.35

            day_rows.append((loc, d, rng.poisson(lam), p_delivery, p_takeaway, refund_p, promo))

    daily = pd.DataFrame(day_rows, columns=["location_id", "date", "n", "p_delivery", "p_takeaway", "refund_p", "promo"])
    rep = np.repeat(np.arange(len(daily)), daily["n"].to_numpy())
    n_orders = len(rep)

    loc_col = daily["location_id"].to_numpy()[rep]
    date_col = pd.to_datetime(daily["date"]).to_numpy()[rep]
    u = rng.random(n_orders)
    p_del = daily["p_delivery"].to_numpy()[rep]
    p_take = daily["p_takeaway"].to_numpy()[rep]
    channel = np.where(u < p_del, "delivery", np.where(u < p_del + p_take, "takeaway", "dine_in"))

    promo_col = daily["promo"].to_numpy()[rep]
    discount = np.where(promo_col & (rng.random(n_orders) < 0.7), 20.0,
                        np.where(rng.random(n_orders) < 0.03, 10.0, 0.0))
    status = np.where(rng.random(n_orders) < daily["refund_p"].to_numpy()[rep], "refunded", "completed")

    seconds = (rng.choice(HOURS, size=n_orders, p=HOUR_WEIGHTS) * 3600
               + rng.integers(0, 60, n_orders) * 60 + rng.integers(0, 60, n_orders))
    ordered_at = pd.to_datetime(date_col) + pd.to_timedelta(seconds, unit="s")

    orders = pd.DataFrame({
        "location_id": loc_col, "ordered_at": ordered_at, "channel": channel,
        "discount_pct": discount, "status": status,
    }).sort_values("ordered_at", kind="stable").reset_index(drop=True)
    orders.insert(0, "id", np.arange(1, n_orders + 1))

    # Order items
    mean_items = orders["channel"].map(MEAN_ITEMS).to_numpy()
    n_items = np.clip(1 + rng.poisson(mean_items - 1), 1, 8)
    order_idx = np.repeat(np.arange(n_orders), n_items)
    prod_ids = np.array([p[0] for p in PRODUCTS])
    prices = np.array([p[3] for p in PRODUCTS])
    weights = np.array([p[5] for p in PRODUCTS], dtype=float)
    weights /= weights.sum()
    prod_idx = rng.choice(len(PRODUCTS), size=len(order_idx), p=weights)
    quantity = np.where(rng.random(len(order_idx)) < 0.15, 2, 1)
    unit_price = prices[prod_idx]

    subtotal = np.bincount(order_idx, weights=quantity * unit_price, minlength=n_orders)
    orders["total"] = np.round(subtotal * (1 - orders["discount_pct"].to_numpy() / 100), 2)
    orders["ordered_at"] = orders["ordered_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

    items = pd.DataFrame({
        "id": np.arange(1, len(order_idx) + 1),
        "order_id": order_idx + 1,
        "location_id": orders["location_id"].to_numpy()[order_idx],
        "product_id": prod_ids[prod_idx],
        "quantity": quantity,
        "unit_price": unit_price,
    })

    locations = pd.DataFrame(LOCATIONS, columns=["id", "name", "city", "opened_on"])
    products = pd.DataFrame([p[:5] for p in PRODUCTS], columns=["id", "name", "category", "price", "unit_cost"])
    users = [(1, "Ava Reynolds", "owner", "", "askdata_owner")]
    users += [(i + 1, MANAGERS[loc], "manager", loc, f"askdata_mgr_{loc}") for i, loc in enumerate(loc_ids, start=1)]
    app_users = pd.DataFrame(users, columns=["id", "name", "role", "location_id", "db_role"])

    for name, frame in [("locations", locations), ("products", products), ("orders", orders),
                        ("order_items", items), ("app_users", app_users)]:
        frame.to_csv(out_dir / f"{name}.csv", index=False)

    def day(k: int) -> str:
        return (end - dt.timedelta(days=k)).isoformat()

    stories = f"""# Planted stories (the answer key)

All data is fictional. These patterns were planted on purpose so the agent has something to find.
Dates are inclusive. Data runs {dates[0].isoformat()} to {end.isoformat()}.

1. **Leeds staffing dip.** {day(leeds_dip[1])} to {day(leeds_dip[0])}: order volume about 30% below the surrounding weeks.
2. **Manchester discount week.** {day(manc_discount[1])} to {day(manc_discount[0])}: 20% discount on roughly 70% of orders,
   order volume about 25% higher, lower average order value and margin.
3. **Glasgow delivery shift.** Over the last {glasgow_days} days the delivery share climbs from about 20% to about 42%.
4. **Bristol refund spike.** {day(bristol_refund_k)}: roughly one in three orders refunded, against a normal 1.5%.
"""
    (out_dir / "planted_stories.md").write_text(stories, encoding="utf-8")

    return {"start": dates[0], "end": end, "orders": n_orders, "items": len(items)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data"))
    parser.add_argument("--end", help="last date of data, YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    end_date = dt.date.fromisoformat(args.end) if args.end else None
    info = generate(Path(args.out), end_date, args.days, args.seed)
    print(f"Wrote {info['orders']:,} orders and {info['items']:,} items ({info['start']} to {info['end']}).")
