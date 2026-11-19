"""Supply MCP server: stock, scheduled arrivals, open orders, and order coverage.

One server process serves one company folder, which contains parts.csv, inbound.csv,
orders.csv, bill-of-materials.csv, and suppliers.csv:

    .venv/bin/python week-05/supply_mcp_server.py                                    # Brookfield (default)
    .venv/bin/python week-05/supply_mcp_server.py --org week-05/copperline-coffee
    .venv/bin/python week-05/supply_mcp_server.py --org week-05/copperline-coffee --check PU-20

Tools:
  get_part(part_id)                    -> description, stock on hand, supplier, scheduled arrivals.
  get_orders_for_part(part_id)         -> open orders that use the part, in ship-day order, with
                                          the parts each order needs from the bill of materials.
  check_coverage(part_id, arrivals?)   -> which orders ship complete on their ship day and, for
                                          each short order, units short and earliest full ship day.

Days are whole days counted from today (today = day 0). The company's column names
differ (e.g. "model" vs "product", "qty" vs "units", "ship_in_days" vs "ship_by_day",
"qty_per_bike" vs "per_unit"), so the columns are detected from each CSV's header.

Coverage rule: orders are filled strictly in ship-day order (ties by order ID). An
order that is short keeps first claim on the next parts to arrive, so it holds up every
later order. Stock available on day d = on hand + every arrival with arrives_in_days <= d.
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent          # week-05/
REPO_ROOT = HERE.parent
DEFAULT_ORG = HERE / "brookfield-bikes"

REQUIRED_FILES = ["parts.csv", "inbound.csv", "orders.csv", "bill-of-materials.csv", "suppliers.csv"]


def log(message: str) -> None:
    # stdout carries the MCP protocol, so all logging goes to stderr.
    print(message, file=sys.stderr, flush=True)


def resolve_path(path_arg: str) -> Path:
    """Accept a path relative to the current directory, the repo root, or week-05/."""
    candidate = Path(path_arg)
    if candidate.is_absolute():
        return candidate.resolve()
    for base in (Path.cwd(), REPO_ROOT, HERE):
        if (base / candidate).exists():
            return (base / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


# ===========================================================================
# CSV loading with columns detected from the headers
# ===========================================================================

def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [(h or "").strip() for h in (reader.fieldnames or [])]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return header, rows


def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")


def find_column(header: list[str], patterns: list[str], file: str, what: str, exclude=()) -> str:
    """Return the one column whose normalized name matches the first pattern that matches."""
    for pattern in patterns:
        hits = [h for h in header if h not in exclude and re.search(pattern, norm(h))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise RuntimeError(f"{file}: more than one {what} column matches: {hits}")
    raise RuntimeError(f"{file}: no {what} column found in header {header}")


def to_int(value: str, file: str, column: str) -> int:
    try:
        number = float(value)
    except ValueError:
        raise RuntimeError(f"{file}: '{value}' in column '{column}' is not a number")
    if not number.is_integer():
        raise RuntimeError(f"{file}: '{value}' in column '{column}' is not a whole number")
    return int(number)


def is_yes(value: str) -> bool:
    return value.strip().lower() in {"yes", "y", "true", "1"}


@dataclass
class Order:
    order_id: str
    customer: str
    contact_email: str
    product: str
    units: int
    ship_day: int
    late_penalty: bool


@dataclass
class Arrival:
    qty: int
    arrives_in_days: int
    po_number: str = ""


class SupplyData:
    """All five CSVs of one company, read with columns detected from the headers."""

    def __init__(self, org_dir: Path):
        self.org_dir = org_dir
        missing = [f for f in REQUIRED_FILES if not (org_dir / f).is_file()]
        if missing:
            raise RuntimeError(f"{org_dir} is missing {', '.join(missing)}")
        self.columns: dict[str, dict[str, str]] = {}
        self._load_parts()
        self._load_inbound()
        self._load_bom()
        self._load_orders()
        self._load_suppliers()

    # -- loaders -------------------------------------------------------------

    def _load_parts(self):
        header, rows = read_csv(self.org_dir / "parts.csv")
        cols = {
            "part": find_column(header, [r"^part_id$", r"part"], "parts.csv", "part ID"),
            "description": find_column(header, [r"desc", r"name"], "parts.csv", "description"),
            "on_hand": find_column(header, [r"on_hand", r"stock", r"in_stock", r"qty|quantity"], "parts.csv", "stock on hand"),
            "supplier": find_column(header, [r"supplier", r"vendor"], "parts.csv", "supplier"),
        }
        self.columns["parts.csv"] = cols
        self.parts = {}
        for r in rows:
            self.parts[r[cols["part"]].upper()] = {
                "part_id": r[cols["part"]].upper(),
                "description": r[cols["description"]],
                "on_hand": to_int(r[cols["on_hand"]], "parts.csv", cols["on_hand"]),
                "supplier": r[cols["supplier"]],
            }

    def _load_inbound(self):
        header, rows = read_csv(self.org_dir / "inbound.csv")
        cols = {
            "po": find_column(header, [r"^po", r"order"], "inbound.csv", "PO number"),
            "part": find_column(header, [r"^part_id$", r"part"], "inbound.csv", "part ID"),
            "supplier": find_column(header, [r"supplier", r"vendor"], "inbound.csv", "supplier"),
            "qty": find_column(header, [r"^qty$|^quantity$|^units$", r"qty|quantity|units"], "inbound.csv", "quantity"),
            "day": find_column(header, [r"arriv", r"day"], "inbound.csv", "arrival day"),
        }
        self.columns["inbound.csv"] = cols
        self.inbound: dict[str, list[Arrival]] = {}
        for r in rows:
            part = r[cols["part"]].upper()
            self.inbound.setdefault(part, []).append(Arrival(
                qty=to_int(r[cols["qty"]], "inbound.csv", cols["qty"]),
                arrives_in_days=to_int(r[cols["day"]], "inbound.csv", cols["day"]),
                po_number=r[cols["po"]],
            ))
        for arrivals in self.inbound.values():
            arrivals.sort(key=lambda a: a.arrives_in_days)

    def _load_bom(self):
        bom_header, bom_rows = read_csv(self.org_dir / "bill-of-materials.csv")
        order_header, _ = read_csv(self.org_dir / "orders.csv")
        part_col = find_column(bom_header, [r"^part_id$", r"part"], "bill-of-materials.csv", "part ID")
        # The product column is the one the bill of materials shares with orders.csv
        # (e.g. "model" at Brookfield, "product" at Copperline).
        shared = [h for h in bom_header if h != part_col and norm(h) in {norm(o) for o in order_header}]
        if len(shared) != 1:
            raise RuntimeError(f"bill-of-materials.csv: expected one product column shared with orders.csv, found {shared}")
        product_col = shared[0]
        per_unit_col = find_column(bom_header, [r"per", r"qty|quantity"], "bill-of-materials.csv",
                                   "parts-per-unit", exclude=(part_col, product_col))
        self.columns["bill-of-materials.csv"] = {"product": product_col, "part": part_col, "per_unit": per_unit_col}
        # bom[part_id][product] = parts per finished unit
        self.bom: dict[str, dict[str, int]] = {}
        for r in bom_rows:
            per = to_int(r[per_unit_col], "bill-of-materials.csv", per_unit_col)
            part = r[part_col].upper()
            self.bom.setdefault(part, {})
            self.bom[part][r[product_col]] = self.bom[part].get(r[product_col], 0) + per
        self.product_label = norm(product_col)        # "model" or "product"

    def _load_orders(self):
        header, rows = read_csv(self.org_dir / "orders.csv")
        product_col = next(h for h in header if norm(h) == norm(self.columns["bill-of-materials.csv"]["product"]))
        cols = {
            "order": find_column(header, [r"^order_id$", r"order"], "orders.csv", "order ID"),
            "customer": find_column(header, [r"^customer$", r"customer|client"], "orders.csv", "customer"),
            "email": find_column(header, [r"email"], "orders.csv", "contact email"),
            "product": product_col,
            "units": find_column(header, [r"^qty$|^quantity$|^units$", r"qty|quantity|units"], "orders.csv",
                                 "order quantity", exclude=(product_col,)),
            "ship_day": find_column(header, [r"ship"], "orders.csv", "ship day"),
            "penalty": find_column(header, [r"penalt"], "orders.csv", "late penalty"),
        }
        self.columns["orders.csv"] = cols
        self.orders = [
            Order(
                order_id=r[cols["order"]],
                customer=r[cols["customer"]],
                contact_email=r[cols["email"]],
                product=r[cols["product"]],
                units=to_int(r[cols["units"]], "orders.csv", cols["units"]),
                ship_day=to_int(r[cols["ship_day"]], "orders.csv", cols["ship_day"]),
                late_penalty=is_yes(r[cols["penalty"]]),
            )
            for r in rows
        ]
        self.orders_by_id = {o.order_id.upper(): o for o in self.orders}

    def _load_suppliers(self):
        header, rows = read_csv(self.org_dir / "suppliers.csv")
        cols = {
            "supplier": find_column(header, [r"^supplier$", r"supplier|vendor|company"], "suppliers.csv", "supplier"),
            "contact": find_column(header, [r"contact_name", r"name"], "suppliers.csv", "contact name",
                                   exclude=()),
            "email": find_column(header, [r"email"], "suppliers.csv", "contact email"),
        }
        self.columns["suppliers.csv"] = cols
        self.suppliers = [
            {"supplier": r[cols["supplier"]], "contact_name": r[cols["contact"]], "contact_email": r[cols["email"]]}
            for r in rows
        ]

    # -- queries -------------------------------------------------------------

    def part(self, part_id: str) -> dict:
        key = (part_id or "").strip().upper()
        if key not in self.parts:
            raise KeyError(f"No part '{part_id}'. Known parts: {', '.join(sorted(self.parts))}")
        return self.parts[key]

    def orders_for_part(self, part_id: str) -> list[tuple[Order, int]]:
        """Open orders that use the part, in ship-day order, with parts per unit."""
        key = self.part(part_id)["part_id"]
        uses = self.bom.get(key, {})
        found = [(o, uses[o.product]) for o in self.orders if o.product in uses]
        found.sort(key=lambda pair: (pair[0].ship_day, pair[0].order_id))
        return found

    def scheduled_arrivals(self, part_id: str) -> list[Arrival]:
        return list(self.inbound.get(self.part(part_id)["part_id"], []))


# ===========================================================================
# Coverage calculation (pure function, no I/O)
# ===========================================================================

def coverage(on_hand: int, arrivals: list[Arrival], orders: list[tuple[Order, int]]) -> list[dict]:
    """Fill orders in ship-day order and report which ship complete, and when.

    For each order (in ship-day order):
      need_through  = parts needed by this order plus every earlier order
      available(d)  = on_hand + arrivals with arrives_in_days <= d
      On its ship day, the order gets whatever is left after earlier orders take their full
      need, so a short order holds up later orders. units_short counts finished units that
      cannot be built by the ship day. ready_day is the first day available(d) >= need_through,
      never earlier than the ship day; None if no scheduled arrival ever covers it.
    """
    arrivals = sorted(arrivals, key=lambda a: a.arrives_in_days)

    def available(day: int) -> int:
        return on_hand + sum(a.qty for a in arrivals if a.arrives_in_days <= day)

    def first_day_with(total: int) -> int | None:
        if on_hand >= total:
            return 0
        running = on_hand
        for a in arrivals:
            running += a.qty
            if running >= total:
                return max(a.arrives_in_days, 0)
        return None

    results = []
    need_before = 0
    for order, per_unit in sorted(orders, key=lambda pair: (pair[0].ship_day, pair[0].order_id)):
        need = order.units * per_unit
        need_through = need_before + need
        left_for_order = max(0, available(order.ship_day) - need_before)
        buildable = min(order.units, left_for_order // per_unit) if per_unit else order.units
        units_short = order.units - buildable
        covered_day = first_day_with(need_through)
        ready_day = None if covered_day is None else max(order.ship_day, covered_day)
        results.append({
            "order_id": order.order_id,
            "customer": order.customer,
            "product": order.product,
            "units": order.units,
            "parts_per_unit": per_unit,
            "parts_needed": need,
            "ship_day": order.ship_day,
            "ships_complete_on_time": units_short == 0,
            "units_short": units_short,
            "ready_day": ready_day,
            "days_late": None if ready_day is None else ready_day - order.ship_day,
            "late_penalty": order.late_penalty,
        })
        need_before = need_through
    return results


# ===========================================================================
# MCP server
# ===========================================================================

class ScheduledArrival(BaseModel):
    po_number: str
    qty: int
    arrives_in_days: int = Field(description="Days from today (today = day 0)")


class PartInfo(BaseModel):
    part_id: str
    description: str
    on_hand: int = Field(description="Units in stock today")
    supplier: str
    scheduled_arrivals: list[ScheduledArrival]


class OrderNeed(BaseModel):
    order_id: str
    customer: str
    product: str = Field(description="The finished product (model) ordered")
    units: int = Field(description="Finished units ordered")
    ship_day: int = Field(description="Day the order is due to ship, in days from today")
    parts_per_unit: int
    parts_needed: int = Field(description="units x parts_per_unit")
    late_penalty: bool


class OrdersForPart(BaseModel):
    part_id: str
    orders: list[OrderNeed] = Field(description="Open orders that use this part, in ship-day order")
    total_parts_needed: int


class ArrivalIn(BaseModel):
    qty: int = Field(ge=0, description="Units arriving")
    arrives_in_days: int = Field(ge=0, description="Arrival day, in days from today (today = day 0)")


class OrderCoverage(BaseModel):
    order_id: str
    customer: str
    product: str
    units: int
    parts_needed: int
    ship_day: int
    ships_complete_on_time: bool
    units_short: int = Field(description="Finished units that cannot be built by the ship day")
    ready_day: int | None = Field(description="Earliest day the full order can ship, in days from today; "
                                              "null if no arrival in the schedule covers it")
    days_late: int | None
    late_penalty: bool


class CoverageResult(BaseModel):
    part_id: str
    on_hand: int
    arrivals_used: list[ArrivalIn]
    schedule: str = Field(description="'scheduled' (inbound.csv) or 'revised' (the arrivals you passed)")
    orders: list[OrderCoverage] = Field(description="Every open order using the part, in ship-day order")
    orders_at_risk: list[str] = Field(description="order_ids that cannot ship complete on their ship day")


READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def build_server(data: SupplyData) -> MCPServer:
    company = data.org_dir.name
    server = MCPServer(
        name=f"{company}-supply",
        instructions=(
            f"Supply data for {company}. Days are counted from today (day 0). Use get_part and "
            "get_orders_for_part to read the data, and check_coverage for every coverage figure."
        ),
    )

    @server.tool(
        name="get_part",
        description=(
            "Get one part's description, stock on hand today, supplier, and scheduled arrivals "
            "(purchase orders with quantity and arrival day, in days from today)."
        ),
        annotations=READ_ONLY,
    )
    def get_part(
        part_id: Annotated[str, Field(description="Part ID, e.g. MC-500 or PU-20")],
    ) -> PartInfo:
        try:
            p = data.part(part_id)
        except KeyError as e:
            raise ToolError(str(e))
        return PartInfo(
            **p,
            scheduled_arrivals=[ScheduledArrival(po_number=a.po_number, qty=a.qty, arrives_in_days=a.arrives_in_days)
                                for a in data.scheduled_arrivals(part_id)],
        )

    @server.tool(
        name="get_orders_for_part",
        description=(
            "List the open customer orders that use a part, in ship-day order, with the parts each "
            "order needs (units x parts per unit from the bill of materials)."
        ),
        annotations=READ_ONLY,
    )
    def get_orders_for_part(
        part_id: Annotated[str, Field(description="Part ID, e.g. MC-500 or PU-20")],
    ) -> OrdersForPart:
        try:
            found = data.orders_for_part(part_id)
        except KeyError as e:
            raise ToolError(str(e))
        orders = [
            OrderNeed(order_id=o.order_id, customer=o.customer, product=o.product, units=o.units,
                      ship_day=o.ship_day, parts_per_unit=per, parts_needed=o.units * per,
                      late_penalty=o.late_penalty)
            for o, per in found
        ]
        return OrdersForPart(part_id=data.part(part_id)["part_id"], orders=orders,
                             total_parts_needed=sum(o.parts_needed for o in orders))

    @server.tool(
        name="check_coverage",
        description=(
            "Work out which open orders for a part ship complete on their ship day, filling orders in "
            "ship-day order from stock on hand plus arrivals. A short order holds up every later order. "
            "For each order: units_short (finished units that can't be built by the ship day) and "
            "ready_day (earliest day the full order can ship). Omit 'arrivals' to use the current "
            "schedule from inbound.csv. To test a revised schedule, pass 'arrivals' as the COMPLETE "
            "list of shipments of this part still to come (it replaces the scheduled ones), with "
            "days counted from today. Check each supplier option with a separate call."
        ),
        annotations=READ_ONLY,
    )
    def check_coverage(
        part_id: Annotated[str, Field(description="Part ID, e.g. MC-500 or PU-20")],
        arrivals: Annotated[list[ArrivalIn] | None, Field(
            description="Optional revised schedule: every shipment of this part still to come, "
                        "e.g. [{\"qty\": 40, \"arrives_in_days\": 4}, {\"qty\": 160, \"arrives_in_days\": 19}]"
        )] = None,
    ) -> CoverageResult:
        try:
            p = data.part(part_id)
            found = data.orders_for_part(part_id)
        except KeyError as e:
            raise ToolError(str(e))
        if arrivals is None:
            used = [Arrival(a.qty, a.arrives_in_days, a.po_number) for a in data.scheduled_arrivals(part_id)]
            schedule = "scheduled"
        else:
            used = [Arrival(a.qty, a.arrives_in_days) for a in arrivals]
            schedule = "revised"
        rows = coverage(p["on_hand"], used, found)
        return CoverageResult(
            part_id=p["part_id"],
            on_hand=p["on_hand"],
            arrivals_used=[ArrivalIn(qty=a.qty, arrives_in_days=a.arrives_in_days)
                           for a in sorted(used, key=lambda a: a.arrives_in_days)],
            schedule=schedule,
            orders=[OrderCoverage(**{k: v for k, v in r.items() if k != "parts_per_unit"}) for r in rows],
            orders_at_risk=[r["order_id"] for r in rows if not r["ships_complete_on_time"]],
        )

    return server


def main():
    parser = argparse.ArgumentParser(description="Supply MCP server: parts, orders, and coverage for one company.")
    parser.add_argument("--org", default=str(DEFAULT_ORG),
                        help="Company folder with the five CSVs (default: week-05/brookfield-bikes)")
    parser.add_argument("--check", metavar="PART_ID",
                        help="Print the coverage of one part on the current schedule and exit (no server)")
    args = parser.parse_args()

    org_dir = resolve_path(args.org)
    if not org_dir.is_dir():
        sys.exit(f"Company folder not found: {args.org}")
    try:
        data = SupplyData(org_dir)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")
    log(f"Company: {org_dir.name}; columns: {json.dumps(data.columns)}")

    if args.check:
        try:
            p = data.part(args.check)
        except KeyError as e:
            sys.exit(str(e))
        rows = coverage(p["on_hand"], data.scheduled_arrivals(args.check), data.orders_for_part(args.check))
        print(json.dumps(rows, indent=2))
        return
    build_server(data).run("stdio")


if __name__ == "__main__":
    main()
