from __future__ import annotations
from datetime import datetime
from typing import Dict, List, Tuple
import yaml, uuid

class CatalogItem:
    def __init__(self, id: uuid.UUID, name: str, daily_price: float, qty: int):
        self.id = id
        self.name = name
        self.price = float(daily_price)
        self.qty = int(qty)

class PricingEngine:
    def __init__(self, settings_path: str):
        self.settings_path = settings_path
        with open(settings_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

        items = self.cfg["inventory"]["items"]
        self.catalog: Dict[uuid.UUID, CatalogItem] = {
            uuid.UUID(i["id"]): CatalogItem(
                id=uuid.UUID(i["id"]), name=i["name"], daily_price=i["daily_price"], qty=i["qty"]
            ) for i in items
        }
        self.blocks: List[dict] = self.cfg["inventory"].get("blocks", [])

    # persistence
    def _rebuild_cfg_items(self):
        self.cfg["inventory"]["items"] = [
            {"id": str(c.id), "name": c.name, "daily_price": c.price, "qty": c.qty}
            for c in self.catalog.values()
        ]
    def save(self):
        self._rebuild_cfg_items()
        with open(self.settings_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.cfg, f, sort_keys=False, allow_unicode=True)

    # CRUD
    def list_items(self) -> List[dict]:
        return [{"id": c.id, "name": c.name, "daily_price": c.price, "qty": c.qty} for c in self.catalog.values()]
    def add_item(self, name: str, daily_price: float, qty: int = 0) -> uuid.UUID:
        new_id = uuid.uuid4()
        self.catalog[new_id] = CatalogItem(new_id, name, daily_price, qty)
        return new_id
    def update_item(self, id: uuid.UUID, name: str | None = None, daily_price: float | None = None, qty: int | None = None):
        if id not in self.catalog: raise ValueError("Unknown item id")
        item = self.catalog[id]
        if name is not None: item.name = name
        if daily_price is not None: item.price = float(daily_price)
        if qty is not None: item.qty = int(qty)
    def delete_item(self, id: uuid.UUID):
        if id not in self.catalog: raise ValueError("Unknown item id")
        del self.catalog[id]

    # helpers
    @staticmethod
    def is_weekend(date_str: str) -> bool:
        d = datetime.fromisoformat(date_str).date()
        return d.weekday() >= 5
    def service_in_area(self, zip_code: str) -> bool:
        prefixes = self.cfg["business"].get("service_area", [])
        return any(zip_code.startswith(p.replace("*", "")) for p in prefixes)
    def estimate_miles(self, customer_zip: str) -> float:
        wh = self.cfg["business"]["warehouse_zip"]
        a, b = customer_zip[:3], wh[:3]
        if a == b: return 5.0
        if abs(int(a) - int(b)) <= 1: return 10.0
        return 20.0

    # availability
    def check_availability(self, date: str, req_items: List[Tuple[uuid.UUID, int]]):
        shortages = []
        reserved: Dict[uuid.UUID, int] = {}
        for b in self.blocks:
            if b["date"] == date:
                bid = uuid.UUID(b["id"]) if isinstance(b["id"], str) else b["id"]
                reserved[bid] = reserved.get(bid, 0) + int(b["qty"])
        for iid, qty in req_items:
            have = self.catalog[iid].qty - reserved.get(iid, 0)
            if qty > have:
                shortages.append({"id": iid, "requested": qty, "available": have})
        return shortages

    # pricing
    def price(self, date: str, zip_code: str, req_items: List[Tuple[uuid.UUID, int]]):
        if not self.service_in_area(zip_code): raise ValueError("Address outside service area")

        items_detail, subtotal = [], 0.0
        for iid, qty in req_items:
            item = self.catalog[iid]
            line = item.price * qty
            items_detail.append({"id": iid, "name": item.name, "qty": qty, "unit": item.price, "line": round(line,2)})
            subtotal += line

        if self.is_weekend(date):
            subtotal *= float(self.cfg["pricing"].get("weekend_multiplier", 1.0))

        min_order = float(self.cfg["business"].get("min_order_subtotal", 0.0))
        discounts_val = 0.0
        if subtotal >= min_order:
            d = datetime.fromisoformat(date).date()
            if d.weekday() <= 3:
                wd = self.cfg["pricing"].get("discounts", {}).get("weekday_pct", 0.0)
                discounts_val = round(subtotal * float(wd), 2)

        setup_minutes = int(self.cfg["pricing"].get("setup_minutes_per_item", 0)) * sum(q for _, q in req_items)
        staff_hourly = float(self.cfg["pricing"].get("staff_hourly", 0.0))
        labor_fee = round((setup_minutes/60.0) * staff_hourly, 2)

        delivery_cfg = self.cfg["pricing"].get("delivery", {})
        fee_override = None
        for band in delivery_cfg.get("bands", []):
            if zip_code.startswith(band["prefix"]): fee_override = float(band["fee"]); break
        if fee_override is not None:
            delivery_fee = fee_override
        else:
            base = float(delivery_cfg.get("base_fee", 0.0))
            per_mile = float(delivery_cfg.get("per_mile", 0.0))
            miles = self.estimate_miles(zip_code)
            delivery_fee = round(base + per_mile * miles, 2)

        taxable = max(subtotal - discounts_val, 0.0) + labor_fee
        tax_rate = float(self.cfg["business"].get("tax_rate", 0.0))
        tax = round(taxable * tax_rate, 2)
        total = round(taxable + delivery_fee + tax, 2)

        return {
            "line_items": items_detail,
            "subtotal": round(subtotal,2),
            "delivery_fee": delivery_fee,
            "labor_fee": labor_fee,
            "discounts": discounts_val,
            "tax": tax,
            "total": total,
        }
