from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from typing import List
import os, json, uuid, re
import runtime_settings as rt
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# voice imports
import asyncio
import edge_tts
from playsound import playsound


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

OPENING_GREETING = "Hi, you've reached Special Events Rental, who am I speaking with?"

# --- Init ---
tenant_mgr = TenantManager(tenants_dir=TENANTS_DIR)
app = FastAPI(title="Phone Bot Tools API (Multi-tenant)", version="0.8.0")

public_dir = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(public_dir):
    app.mount("/demo", StaticFiles(directory=public_dir, html=True), name="demo")

oai = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Reasoning helpers ----------
def _build_reason_messages(eng: PricingEngine, req: ReasonRequest) -> list[dict]:
    biz = eng.cfg.get("business", {})
    catalog_items = [v.name for v in eng.catalog.values()]
    sys = (
        "You are the brain of a phone sales agent for an event-rental business. "
        f"Business: {biz.get('name','N/A')}; Hours: {biz.get('hours','N/A')}; "
        f"Service area prefixes: {biz.get('service_area',[])}. "
        "Return STRICT JSON: {say: string, tool?: string, args?: object}. "
        "Tools: quote, check_availability, create_lead, book. Be concise. "
        "If the customer requests something ambiguous (like 'chairs' or 'tables'), "
        f"ask them to clarify by choosing from available options: {catalog_items}. "
        "Never assume the type if multiple catalog items could apply. "
        "Speak in a natural receptionist style, like a real conversation."
    )
    return (
        [{"role": "system", "content": sys},
         {"role": "system", "content": f"Goal: {req.goal}"}]
        + [m.model_dump() for m in req.messages]
    )


def _reason_with_openai(messages: list[dict]) -> Thought:
    r = oai.chat.completions.create(
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
        max_tokens=200,
    )
    return Thought(**json.loads(r.choices[0].message.content))

async def _speak(text: str, voice: str = "en-US-JennyNeural"):
    """Generate and play voice for the given text locally using Edge-TTS."""
    out_file = "tmp_voice.mp3"
    communicate = edge_tts.Communicate(text, voice, rate="+5%", pitch="+10Hz")
    await communicate.save(out_file)
    playsound(out_file)
    os.remove(out_file)


# ---------- Catalog normalization ----------
def _canon(s: str) -> list[str]:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    toks = [t for t in s.split() if t]
    out = []
    for t in toks:
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        out.append(t)
    return out

def _normalize_items(eng: PricingEngine, args: dict) -> list[dict]:
    items_in = []
    if "items" in args and isinstance(args["items"], list):
        items_in = args["items"]
    elif "item" in args and ("quantity" in args or "qty" in args):
        items_in = [{"name": args.get("item"), "qty": int(args.get("quantity") or args.get("qty") or 1)}]
    else:
        return []

    name_to_id_exact = {v.name.lower(): k for k, v in eng.catalog.items()}
    catalog_tokens = {k: set(_canon(v.name)) for k, v in eng.catalog.items()}

    out = []
    for it in items_in:
        if it.get("id") and it["id"] in eng.catalog:
            out.append({"id": it["id"], "qty": int(it.get("qty", it.get("quantity", 1)))})
            continue

        nm = (it.get("name") or it.get("item") or "").strip()
        if nm:
            iid = name_to_id_exact.get(nm.lower())
            if iid:
                out.append({"id": iid, "qty": int(it.get("qty", it.get("quantity", 1)))})
                continue

            qtokens = set(_canon(nm))
            best_id, best_score = None, 0
            for cid, ctoks in catalog_tokens.items():
                score = len(qtokens & ctoks)
                if score > best_score:
                    best_score = score
                    best_id = cid
            if best_id and best_score >= 2:
                out.append({"id": best_id, "qty": int(it.get("qty", it.get("quantity", 1)))})
                continue

        if nm and nm in eng.catalog:
            out.append({"id": nm, "qty": int(it.get("qty", it.get("quantity", 1)))})
            continue

        raise HTTPException(400, f"Unknown item in request: {it}")

    return out

def _normalize_zip(args: dict) -> str | None:
    z = args.get("zip") or args.get("postal") or args.get("area") \
        or args.get("location") or args.get("location_prefix")
    if not z: 
        return None
    digits = "".join(ch for ch in str(z) if ch.isdigit())
    return digits[:5] if digits else str(z)

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
    try:
        datetime.fromisoformat(s)
        return s
    except Exception:
        return s

def _run_tool(eng: PricingEngine, tool: str | None, args: dict) -> dict:
    # Normalize nulls
    if not tool or str(tool).lower() == "null":
        return {}

    tool = tool.strip()

    # ---- check_availability ----
    if tool == "check_availability":
        date = _normalize_date(args.get("date") or args.get("delivery_date"))
        if not date:
            raise HTTPException(400, "check_availability requires 'date'")
        items = _normalize_items(eng, args)
        req = [(x["id"], x["qty"]) for x in items]
        shortages = eng.check_availability(date, req)
        return {"available": len(shortages) == 0, "shortages": shortages, "substitutions": []}

    # ---- quote ----
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

    # ---- create_lead ----
    if tool == "create_lead":
        lead = repo.create_lead(
            args.get("name") or "Caller",
            args.get("phone") or args.get("caller") or "",
            args.get("email"),
            args.get("quote_id"),
        )
        return {"lead_id": lead.lead_id}

    # ---- book ----
    if tool == "book":
        order = repo.create_order(args.get("quote_id") or "")
        return {"order_id": order.order_id}

    raise HTTPException(400, f"Unknown tool '{tool}'")

# ---------- Endpoints ----------
@app.get("/healthz")
def healthz():
    return {"ok": True, "tenants": tenant_mgr.list_tenants()}

async def get_engine(request: Request) -> PricingEngine:
    t_name = resolve_tenant_name(request, header_name=TENANT_HEADER, use_did=TENANT_FROM_DID)
    if not t_name:
        raise HTTPException(400, "Missing tenant. Provide X-Tenant header or X-Caller-DID.")
    return tenant_mgr.get_engine(t_name)

from fastapi import Form
from fastapi.responses import Response

@app.post("/twilio/voice")
async def twilio_voice(From: str = Form(...), To: str = Form(...)):
    """
    Entry point for incoming Twilio calls.
    Just responds with a simple greeting for now.
    """
    twiml = """
    <Response>
      <Say voice="Polly.Joanna">Hi! You’ve reached Special Events Rental. This is a test call.</Say>
    </Response>
    """
    return Response(content=twiml.strip(), media_type="application/xml")


# --- Conversational Entry Point ---
@app.post("/dialog")
async def dialog(req: ReasonRequest, request: Request):
    """
    Main conversational loop:
    - If no messages yet → greet caller.
    - Otherwise → reason over messages, possibly run tools, return reply.
    """
    eng: PricingEngine = await get_engine(request)

    # ---- 1️⃣ Greeting ----
    if not req.messages:
        say = OPENING_GREETING
        if request.query_params.get("local_voice_test") == "True":
            try:
                asyncio.create_task(_speak(say))
            except Exception as e:
                print(f"[VOICE] synthesis failed: {e}")
        return {
            "say": say,
            "tool": None,
            "args": None,
            "tool_result": None,
        }

    # ---- 2️⃣ Reasoning phase ----
    messages = _build_reason_messages(eng, req)
    try:
        # handle both sync and async mocks
        maybe_thought = _reason_with_openai(messages)
        if asyncio.iscoroutine(maybe_thought):
            thought = await maybe_thought
        else:
            thought = maybe_thought
    except Exception as e:
        # fallback path: still email, then return 500
        caller_number = request.headers.get("X-Caller-Number", "unknown")
        subject = "Incomplete lead (reasoning error)"
        body = f"Reasoning failed for caller {caller_number}.\nError: {e}"
        try:
            send_lead_email(subject, body)
        except Exception as email_err:
            print(f"[EMAIL STUB ERROR] {email_err}")

        return JSONResponse(
            status_code=500,
            content={"error": f"reason error: {str(e)}"}
        )

    # ---- 3️⃣ Tool execution ----
    tool = thought.tool if thought.tool and str(thought.tool).lower() != "null" else None
    tool_result = None
    if tool:
        try:
            tool_result = _run_tool(eng, tool, thought.args or {})
        except Exception as e:
            tool_result = {"error": str(e)}

    # ---- 4️⃣ Follow-up logic ----
    followup = None
    if tool == "check_availability" and tool_result and tool_result.get("available"):
        try:
            followup = _run_tool(eng, "quote", thought.args or {})
        except Exception:
            pass

    # ---- 5️⃣ Lead email ----
    if tool == "create_lead" and tool_result:
        args = thought.args or {}
        subject = f"New Lead: {args.get('name','Unknown')} ({args.get('date','N/A')})"
        body = json.dumps(args, indent=2)
        try:
            send_lead_email(subject, body)
        except Exception as e:
            print(f"[EMAIL ERROR] Lead email failed: {e}")

    # ---- 6️⃣ Voice synthesis ----
    if request.query_params.get("local_voice_test") == "True":
        try:
            asyncio.create_task(_speak(thought.say))
        except Exception as e:
            print(f"[VOICE] synthesis failed: {e}")

    # ---- 7️⃣ Return structured response ----
    return {
        "say": thought.say,
        "tool": tool,
        "args": thought.args,
        "tool_result": tool_result,
        "followup_quote": followup,
    }


# ---------- Lead notification ----------
def send_lead_email(subject: str, body: str):
    """
    Temporary stub for testing / dev.
    Later this can send via SMTP, SES, SendGrid, etc.
    """
    print(f"[LEAD EMAIL]\nSubject: {subject}\nBody:\n{body}\n")
    return True

# --------- Availability, Quote, Leads, Admin ---------
@app.post("/check_availability", response_model=AvailabilityOut)
async def check_availability(inp: AvailabilityIn, request: Request):
    eng = await get_engine(request)
    req: List[tuple[uuid.UUID, int]] = [(it.id, it.qty) for it in inp.items]
    shortages = eng.check_availability(inp.date, req)
    return AvailabilityOut(available=(len(shortages) == 0), shortages=shortages, substitutions=[])

@app.post("/quote", response_model=MoneyOut)
async def quote(inp: QuoteIn, request: Request):
    eng = await get_engine(request)
    req: List[tuple[uuid.UUID, int]] = [(it.id, it.qty) for it in inp.items]
    shortages = eng.check_availability(inp.date, req)
    priced = eng.price(inp.date, inp.zip, req)
    if shortages:
        priced["note"] = "Some items are short; consider substitutions."
        return JSONResponse(status_code=206, content=priced)
    return priced

@app.post("/create_lead", response_model=LeadOut)
async def create_lead(inp: LeadIn):
    lead = repo.create_lead(inp.name, inp.phone, inp.email, inp.quote_id)
    return LeadOut(lead_id=lead.lead_id)

@app.post("/book", response_model=BookOut)
async def book(inp: BookIn):
    order = repo.create_order(inp.quote_id)
    return BookOut(order_id=order.order_id)

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


############################################# We start ngrok automatically for Twilio testing #############################################
from pyngrok import ngrok, conf
import os

rt.NGROK_AUTHTOKEN
conf.get_default().auth_token = rt.NGROK_AUTHTOKEN
public_url = ngrok.connect(8000).public_url
print(f"[NGROK] Public URL: {public_url}")
print("Set this as your Twilio webhook:\n" f"  {public_url}/twilio/voice\n")
