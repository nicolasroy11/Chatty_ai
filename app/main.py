from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from typing import List
import os, json, uuid
import runtime_settings as rt

from openai import OpenAI

from .schemas import (
    ReasonRequest, Thought, QuoteIn, MoneyOut, AvailabilityIn, AvailabilityOut,
    LeadIn, LeadOut, BookIn, BookOut, ItemCreate, ItemUpdate, ItemDef
)
from .pricing import PricingEngine
from .repo import repo
from .tenancy import TenantManager, resolve_tenant_name

# --- Environment config ---
OPENAI_API_KEY = rt.OPENAI_API_KEY
OPENAI_MODEL = rt.OPENAI_MODEL
TENANTS_DIR = rt.TENANTS_DIR
TENANT_HEADER = rt.TENANT_HEADER
TENANT_FROM_DID = rt.TENANT_FROM_DID
ADMIN_API_KEY = rt.ADMIN_API_KEY

# --- Init ---
tenant_mgr = TenantManager(tenants_dir=TENANTS_DIR)
app = FastAPI(title="Phone Bot Tools API (Multi-tenant)", version="0.6.0")

public_dir = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(public_dir):
    app.mount("/demo", StaticFiles(directory=public_dir, html=True), name="demo")

oai = OpenAI(api_key=OPENAI_API_KEY)


# ---------- helpers ----------
def _build_reason_messages(eng: PricingEngine, req: ReasonRequest) -> list[dict]:
    biz = eng.cfg.get("business", {})
    sys = (
        "You are the brain of a phone sales agent for an event-rental business. "
        f"Business: {biz.get('name','N/A')}; Hours: {biz.get('hours','N/A')}; "
        f"Service area prefixes: {biz.get('service_area',[])}. "
        "Return STRICT JSON: {say: string, tool?: string, args?: object}. "
        "Tools: quote, check_availability, create_lead, book. Be concise."
    )
    return (
        [{"role": "system", "content": sys},
         {"role": "system", "content": f"Goal: {req.goal}"}]
        + [m.model_dump() for m in req.messages]
    )

def _reason_with_openai(messages: list[dict]) -> Thought:
    # Uses your global OPENAI_… settings already loaded
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    r = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Thought",
                "schema": {
                    "type": "object",
                    "required": ["say"],
                    "properties": {
                        "say": {"type": "string"},
                        "tool": {"type": ["string", "null"]},
                        "args": {"type": ["object", "null"]}
                    },
                    "additionalProperties": False
                }
            }
        },
        max_tokens=150,
    )
    import json as _json
    return Thought(**_json.loads(r.choices[0].message.content))

# ------- Tool chaining helpers ------- TODO: refactor for cleaner design

import re

def _canon(s: str) -> list[str]:
    """
    Canonicalize a name into tokens:
    - lowercase
    - remove punctuation
    - split to words
    - naive singularize (drop trailing 's')
    """
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)   # kill punctuation/quotes/() etc.
    toks = [t for t in s.split() if t]
    # naive singularization
    out = []
    for t in toks:
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        out.append(t)
    return out

def _normalize_items(eng: PricingEngine, args: dict) -> list[dict]:
    """
    Accepts either:
      - {"item": "Resin Folding Chair (White)", "quantity": 50}
      - {"items": [{"name":"white resin chairs","qty": 50}, {"id":"chair_resin", "qty": 10}]}
      - {"item": "chair_resin", "quantity": 50}
    Returns: [{"id": "<catalog_id>", "qty": int}, ...]
    Uses fuzzy matching against catalog names if id is not given.
    """
    # collect incoming items
    items_in = []
    if "items" in args and isinstance(args["items"], list):
        items_in = args["items"]
    elif "item" in args and ("quantity" in args or "qty" in args):
        items_in = [{"name": args.get("item"), "qty": int(args.get("quantity") or args.get("qty") or 1)}]
    else:
        return []

    # build lookup structures
    name_to_id_exact = {v.name.lower(): k for k, v in eng.catalog.items()}
    # precompute canonical tokens for each catalog item
    catalog_tokens = {}
    for k, v in eng.catalog.items():
        catalog_tokens[k] = set(_canon(v.name))

    out = []
    for it in items_in:
        # 1) direct id pass-through
        if it.get("id") and it["id"] in eng.catalog:
            out.append({"id": it["id"], "qty": int(it.get("qty", it.get("quantity", 1)))})
            continue

        # 2) exact name match
        nm = (it.get("name") or it.get("item") or "").strip()
        if nm:
            iid = name_to_id_exact.get(nm.lower())
            if iid:
                out.append({"id": iid, "qty": int(it.get("qty", it.get("quantity", 1)))})
                continue

            # 3) fuzzy token overlap match
            qtokens = set(_canon(nm))
            # score by token overlap size; prefer larger overlaps and longer catalog names
            best_id = None
            best_score = 0
            for cid, ctoks in catalog_tokens.items():
                score = len(qtokens & ctoks)
                if score > best_score:
                    best_score = score
                    best_id = cid

            # require at least 2 overlapping tokens to avoid silly matches
            if best_id and best_score >= 2:
                out.append({"id": best_id, "qty": int(it.get("qty", it.get("quantity", 1)))})
                continue

        # 4) last-chance: if the provided string is actually a catalog id
        if nm and nm in eng.catalog:
            out.append({"id": nm, "qty": int(it.get("qty", it.get("quantity", 1)))})
            continue

        # Could not resolve
        # Build a few suggestions (top 3 by overlap)
        if nm:
            qtokens = set(_canon(nm))
            scored = sorted(
                [(cid, len(qtokens & ctoks)) for cid, ctoks in catalog_tokens.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            suggestions = [{"id": cid, "name": eng.catalog[cid].name} for cid, sc in scored if sc > 0]
        else:
            suggestions = []
        raise HTTPException(400, f"Unknown item in request: {it}. Suggestions: {suggestions}")

    return out

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def _normalize_zip(args: dict) -> str | None:
    z = args.get("zip") or args.get("postal") or args.get("area") \
        or args.get("location") or args.get("location_prefix")
    if not z: 
        return None
    z = str(z).strip()
    # keep first 5 digits if a ZIP+4 or prefix was provided
    digits = "".join(ch for ch in z if ch.isdigit())
    return digits[:5] if digits else z

_WEEKDAYS = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}

def _next_weekday_iso(target_weekday: int, tz="America/Los_Angeles") -> str:
    now = datetime.now(ZoneInfo(tz))
    days_ahead = (target_weekday - now.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).date().isoformat()

def _normalize_date(ds: str | None, tz="America/Los_Angeles") -> str | None:
    if not ds: 
        return None
    s = ds.lower().strip()
    if s.startswith("next "):
        w = s.split(" ", 1)[1]
        if w in _WEEKDAYS:
            return _next_weekday_iso(_WEEKDAYS[w], tz=tz)
    # already ISO?
    try:
        datetime.fromisoformat(s)
        return s
    except Exception:
        return s  # leave as-is if we can’t parse


def _run_tool(eng: PricingEngine, tool: str, args: dict) -> dict:
    tool = (tool or "").strip()
    if not tool:
        return {}

    # ---- check_availability ----
    if tool == "check_availability":
        date = args.get("date") or args.get("delivery_date")
        if not date:
            raise HTTPException(400, "check_availability requires 'date'")
        items = _normalize_items(eng, args)
        req = [(x["id"], x["qty"]) for x in items]
        shortages = eng.check_availability(date, req)
        return {
            "available": len(shortages) == 0,
            "shortages": shortages,
            "substitutions": [],
        }

    # ---- create_lead ----
    if tool == "create_lead":
        name = args.get("name") or "Caller"
        phone = args.get("phone") or args.get("caller") or ""
        email = args.get("email")
        quote_id = args.get("quote_id")
        lead = repo.create_lead(name, phone, email, quote_id)
        return {"lead_id": lead.lead_id}

    # ---- book ----
    if tool == "book":
        quote_id = args.get("quote_id") or ""
        payment_token = args.get("payment_token") or "demo"
        order = repo.create_order(quote_id)
        return {"order_id": order.order_id, "payment_token_used": payment_token}
    
    # ----- check_availability -----
    if tool == "check_availability":
        date = _normalize_date(args.get("date") or args.get("delivery_date"))
        if not date:
            raise HTTPException(400, "check_availability requires 'date'")
        items = _normalize_items(eng, args)
        req = [(x["id"], x["qty"]) for x in items]
        shortages = eng.check_availability(date, req)
        return {"available": len(shortages) == 0, "shortages": shortages, "substitutions": []}

    # ----- quote -----
    if tool == "quote":
        date = _normalize_date(args.get("date") or args.get("delivery_date"))
        zip_code = _normalize_zip(args)
        if not (date and zip_code):
            raise HTTPException(400, "quote requires 'date' and a ZIP")
        items = _normalize_items(eng, args)
        req = [(x["id"], x["qty"]) for x in items]
        shortages = eng.check_availability(date, req)
        priced = eng.price(date, zip_code, req)
        if shortages:
            priced["note"] = "Some items are short; consider substitutions."
        return priced

    raise HTTPException(400, f"Unknown tool '{tool}'")


@app.get("/healthz")
def healthz():
    return {"ok": True, "tenants": tenant_mgr.list_tenants()}

async def get_engine(request: Request) -> PricingEngine:
    t_name = resolve_tenant_name(request, header_name=TENANT_HEADER, use_did=TENANT_FROM_DID)
    if not t_name:
        raise HTTPException(400, "Missing tenant. Provide X-Tenant header or X-Caller-DID.")
    return tenant_mgr.get_engine(t_name)

# --------- Reason (OpenAI) ----------
@app.post("/reason", response_model=Thought)
async def reason(req: ReasonRequest, request: Request):
    eng: PricingEngine = await get_engine(request)
    messages = _build_reason_messages(eng, req)
    try:
        return _reason_with_openai(messages)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    
@app.post("/reason_and_act")
async def reason_and_act(req: ReasonRequest, request: Request):
    eng: PricingEngine = await get_engine(request)
    messages = _build_reason_messages(eng, req)

    try:
        thought = _reason_with_openai(messages)
    except Exception as e:
        return JSONResponse({"error": f"reason error: {e}"}, status_code=500)

    tool_result = None
    if thought.tool:
        tool_result = _run_tool(eng, thought.tool, thought.args or {})

    # If the model asked to check availability and it's available,
    # immediately follow with a quote using the same args.
    followup = None
    if thought.tool == "check_availability" and tool_result and tool_result.get("available"):
        try:
            followup = _run_tool(eng, "quote", thought.args or {})
        except Exception:
            pass  # don’t fail the request; just skip follow-up

    return {
        "say": thought.say,
        "tool": thought.tool,
        "args": thought.args,
        "tool_result": tool_result,
        "followup_quote": followup,
    }



# --------- Availability ----------
@app.post("/check_availability", response_model=AvailabilityOut)
async def check_availability(inp: AvailabilityIn, request: Request):
    eng = await get_engine(request)
    req: List[tuple[uuid.UUID, int]] = []
    for it in inp.items:
        iid = it.id
        if not iid and it.name:
            for k, v in eng.catalog.items():
                if v.name.lower() == it.name.lower():
                    iid = k
                    break
        if not iid:
            raise HTTPException(400, f"Unknown item: {it}")
        req.append((iid, it.qty))
    shortages = eng.check_availability(inp.date, req)
    return AvailabilityOut(available=(len(shortages) == 0), shortages=shortages, substitutions=[])

# --------- Quote ----------
@app.post("/quote", response_model=MoneyOut)
async def quote(inp: QuoteIn, request: Request):
    eng = await get_engine(request)
    req: List[tuple[uuid.UUID, int]] = []
    for it in inp.items:
        iid = it.id
        if not iid and it.name:
            for k, v in eng.catalog.items():
                if v.name.lower() == it.name.lower():
                    iid = k
                    break
        if not iid:
            raise HTTPException(400, f"Unknown item: {it}")
        req.append((iid, it.qty))
    shortages = eng.check_availability(inp.date, req)
    priced = eng.price(inp.date, inp.zip, req)
    if shortages:
        priced["note"] = "Some items are short; consider substitutions."
        return JSONResponse(status_code=206, content=priced)
    return priced

# --------- Lead & Booking ----------
@app.post("/create_lead", response_model=LeadOut)
async def create_lead(inp: LeadIn):
    lead = repo.create_lead(inp.name, inp.phone, inp.email, inp.quote_id)
    return LeadOut(lead_id=lead.lead_id)

@app.post("/book", response_model=BookOut)
async def book(inp: BookIn):
    order = repo.create_order(inp.quote_id)
    return BookOut(order_id=order.order_id)

# --------- Admin: Inventory CRUD ----------
@app.get("/admin/inventory", response_model=List[ItemDef])
async def admin_list_inventory(request: Request):
    if request.headers.get("X-Admin-Key", "") != ADMIN_API_KEY:
        raise HTTPException(401, "Admin key required")
    eng = await get_engine(request)
    return [ItemDef(**it) for it in eng.list_items()]

@app.post("/admin/inventory", response_model=ItemDef)
async def admin_create_item(data: ItemCreate, request: Request):
    if request.headers.get("X-Admin-Key", "") != ADMIN_API_KEY:
        raise HTTPException(401, "Admin key required")
    eng = await get_engine(request)
    new_id = eng.add_item(name=data.name, daily_price=data.daily_price, qty=data.qty)
    eng.save()
    return ItemDef(id=new_id, name=data.name, daily_price=data.daily_price, qty=data.qty)

@app.put("/admin/inventory/{item_id}", response_model=ItemDef)
async def admin_update_item(item_id: uuid.UUID, data: ItemUpdate, request: Request):
    if request.headers.get("X-Admin-Key", "") != ADMIN_API_KEY:
        raise HTTPException(401, "Admin key required")
    eng = await get_engine(request)
    eng.update_item(id=item_id, name=data.name, daily_price=data.daily_price, qty=data.qty)
    eng.save()
    cur = next((it for it in eng.list_items() if it["id"] == item_id), None)
    if not cur:
        raise HTTPException(404, "Unknown item id")
    return ItemDef(**cur)

@app.delete("/admin/inventory/{item_id}")
async def admin_delete_item(item_id: uuid.UUID, request: Request):
    if request.headers.get("X-Admin-Key", "") != ADMIN_API_KEY:
        raise HTTPException(401, "Admin key required")
    eng = await get_engine(request)
    eng.delete_item(item_id)
    eng.save()
    return {"ok": True, "deleted": str(item_id)}
