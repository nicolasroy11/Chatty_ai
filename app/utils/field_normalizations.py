# ---------------- Normalization Utilities ----------------
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from app.pricing import PricingEngine


def _canon(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t]
    normalized = [t[:-1] if t.endswith("s") and len(t) > 3 else t for t in tokens]
    return normalized


def _normalize_items(eng: PricingEngine, args: dict) -> list[dict]:
    if not args:
        return []
    items_in = []
    if "items" in args and isinstance(args["items"], list):
        items_in = args["items"]
    elif "item" in args:
        qty = int(args.get("quantity") or args.get("qty") or 1)
        items_in = [{"name": args["item"], "qty": qty}]
    if not items_in:
        return []

    name_to_id = {v.name.lower(): k for k, v in eng.catalog.items()}
    catalog_tokens = {k: set(_canon(v.name)) for k, v in eng.catalog.items()}
    out = []

    for it in items_in:
        name = (it.get("name") or "").strip().lower()
        qty = int(it.get("qty", 1))
        if name in name_to_id:
            out.append({"id": name_to_id[name], "qty": qty})
            continue
        tokens = set(_canon(name))
        best_id, best_score = None, 0
        for cid, ctoks in catalog_tokens.items():
            score = len(tokens & ctoks)
            if score > best_score:
                best_score, best_id = score, cid
        if best_id and best_score >= 2:
            out.append({"id": best_id, "qty": qty})
    return out


def _normalize_zip(args: dict) -> str | None:
    z = args.get("zip") or args.get("postal") or args.get("area") or args.get("location")
    if not z:
        return None
    digits = "".join(ch for ch in str(z) if ch.isdigit())
    return digits[:5] if digits else str(z)


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def _next_weekday_iso(target_weekday: int, tz="America/Los_Angeles") -> str:
    now = datetime.now(ZoneInfo(tz))
    days_ahead = (target_weekday - now.weekday() + 7) % 7 or 7
    return (now + timedelta(days=days_ahead)).date().isoformat()


def _normalize_date(ds: str | None, tz="America/Los_Angeles") -> str | None:
    if not ds:
        return None
    s = ds.lower().strip()
    if s.startswith("next "):
        w = s.split(" ", 1)[1]
        if w in _WEEKDAYS:
            return _next_weekday_iso(_WEEKDAYS[w], tz)
    try:
        datetime.fromisoformat(s)
        return s
    except Exception:
        return s