from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import Response
from starlette.staticfiles import StaticFiles
from typing import List
import os, json
from app.classes.session import SessionState
from app.classes.turn import Turn
from app.tenant_workflow import TenantWorkflow
from app.pricing import PricingEngine
from app.repo import repo
from app.tenancy import TenantManager, resolve_tenant_name
from app.schemas import (ReasonRequest, Thought, LeadIn, LeadOut)
from app.utils import tts
from app.utils.tts import synthesize_speech
import runtime_settings as rt
from openai import OpenAI
from fastapi import WebSocket


PREPARED_AUDIO_URL: dict[str, str] = {}  # CallSid -> audio URL ready to play

# ---------------- Environment ----------------
OPENAI_API_KEY = rt.OPENAI_API_KEY
OPENAI_MODEL = rt.OPENAI_MODEL
TENANTS_DIR = rt.TENANTS_DIR
TENANT_HEADER = rt.TENANT_HEADER
TENANT_FROM_DID = rt.TENANT_FROM_DID
ADMIN_API_KEY = rt.ADMIN_API_KEY

DIALOG_SESSIONS: dict[str, SessionState] = {}
tenant_mgr = TenantManager(tenants_dir=TENANTS_DIR)
oai = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Phone Bot Tools API (Multi-tenant)", version="1.0.0")
app.mount("/audio", StaticFiles(directory=tts.AUDIO_DIR), name="audio")

public_dir = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(public_dir):
    app.mount("/demo", StaticFiles(directory=public_dir, html=True), name="demo")


# ---------------- Session Helpers ----------------
def get_or_create_session(call_id: str, caller_number: str) -> SessionState:
    if call_id not in DIALOG_SESSIONS:
        session = SessionState(call_id=call_id, caller_number=caller_number)
        DIALOG_SESSIONS[call_id] = session
        print(f"[{call_id}] SESSION CREATED: ", session.to_dict())
    else:
        session = DIALOG_SESSIONS[call_id]
        print(f"[{call_id}] SESSION RESTORED: ", session.to_dict())
    return session


async def get_engine(request: Request) -> PricingEngine:
    t_name = resolve_tenant_name(request, header_name=TENANT_HEADER, use_did=TENANT_FROM_DID)
    if not t_name:
        raise HTTPException(400, "Missing tenant. Provide X-Tenant header or X-Caller-DID.")
    return tenant_mgr.get_engine(t_name)


# ---------------- Twilio Voice ----------------

@app.websocket("/test_ws")
async def test_ws(ws: WebSocket):
    await ws.accept()
    print("ws connected")
    while True:
        msg = await ws.receive_text()
        print("msg:", msg)
        await ws.send_text("pong")


@app.post("/twilio/voice")
async def twilio_voice(From: str = Form(...), To: str = Form(...), CallSid: str = Form(...)):
    print(f"Incoming call from {From}, CallSid={CallSid}")
    get_or_create_session(CallSid, From)

    handle_url = f"{rt.ENV.URL}/twilio/handle_speech"
    ws_url = rt.ENV.URL.replace("https", "wss").replace("http", "ws") + "/twilio/stream"

    twiml = f"""
        <Response>
        <Start>
            <Stream url="{ws_url}" track="inbound_audio"/>
        </Start>
        <Say voice="Polly.Matthew">{rt.TENANT.OPENING_GREETING}</Say>
        <Gather input="speech" action="{handle_url}" speechTimeout="auto" />
        </Response>
        """

    return Response(content=twiml.strip(), media_type="application/xml")


@app.post("/twilio/handle_speech")
async def twilio_handle_speech(SpeechResult: str = Form(None), From: str = Form(None), CallSid: str = Form(None)):
    user_text = (SpeechResult or "").strip()
    print(f"Caller {From} said: {user_text}")

    session = get_or_create_session(CallSid, From)

    # If Media Streams prepped an answer/audio, play it instantly.
    pre = PREPARED_AUDIO_URL.pop(CallSid, None)
    if pre:
        # Decide if conversation continues or not
        wf = TenantWorkflow()
        done = wf.is_complete(session)
        return Response(content=build_twiml_response(pre, done), media_type="application/xml")

    # Fallback to current (non-streaming) path
    session.add_message("user", user_text)
    workflow = TenantWorkflow()
    say_text = workflow.handle_step(session, user_text)
    session.add_message("assistant", say_text)
    session.say = say_text
    audio_filename = f"{CallSid}_{len(session.messages)}"
    audio_path = synthesize_speech(say_text, audio_filename)
    audio_url = f"{rt.ENV.URL}/audio/{os.path.basename(audio_path)}"
    return Response(content=build_twiml_response(audio_url, workflow.is_complete(session)), media_type="application/xml")


def build_twiml_response(audio_url: str, call_complete: bool) -> str:
    """Return properly formatted TwiML for either ongoing or final response."""
    if call_complete:
        # final message before hangup
        return f"""
        <Response>
            <Play>{audio_url}</Play>
            <Hangup/>
        </Response>
        """.strip()
    else:
        # continue gathering user input
        return f"""
        <Response>
            <Play>{audio_url}</Play>
            <Gather input="speech"
                    action="{rt.ENV.URL}/twilio/handle_speech"
                    speechTimeout="auto" />
        </Response>
        """.strip()


@app.websocket("/twilio/stream")
async def twilio_stream(websocket: WebSocket):
    await websocket.accept()
    params = dict(websocket.query_params)
    call_id = params.get("CallSid", "local")
    caller = params.get("From", "")
    session = get_or_create_session(call_id, caller)

    # TODO: replace with local ASR (whisper.cpp?)
    partial_text = []
    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            et = data.get("event")
            if et == "start":
                continue
            if et == "media":
                # feed audio to ASR
                # chunk = base64.b64decode(data["media"]["payload"])
                # asr.feed(chunk)
                continue
            if et == "mark":
                continue
            if et == "stop":
                # End this streaming session
                break
    except Exception:
        pass
    finally:
        # final_text = asr.final_result()
        final_text = " " .join(partial_text).strip()  # placeholder if buffering partials
        if final_text:
            # Pre-run normal workflow and synth so it’s ready for /handle_speech
            wf = TenantWorkflow()
            say_text = wf.handle_step(session, final_text)
            session.add_message("user", final_text)
            session.add_message("assistant", say_text)

            audio_filename = f"{call_id}_{len(session.messages)}"
            audio_path = synthesize_speech(say_text, audio_filename)
            audio_url = f"{rt.ENV.URL}/audio/{os.path.basename(audio_path)}"
            PREPARED_AUDIO_URL[call_id] = audio_url

        await websocket.close()



@app.post("/twilio/hangup")
async def twilio_hangup(CallSid: str = Form(None)):
    session = DIALOG_SESSIONS.pop(CallSid, None)
    if not session:
        print(f"Call {CallSid} ended. No session found.")
        return Response("<Response></Response>", media_type="application/xml")

    print(f"\nCall {CallSid} ended.\nSummary:\n{session.summary()}\n")
    return Response("<Response></Response>", media_type="application/xml")


# ---------------- LLM Prompt Builder ----------------
def _build_llm_prompt_messages(workflow: TenantWorkflow, req: ReasonRequest) -> List[dict]:
    """Builds the chat messages sent to the LLM for reasoning or extraction."""
    biz_name = workflow.tenant_name
    slot_list = ", ".join(s.name for s in workflow.slots)
    slot_json = ", ".join(f'"{s.name}": "..."' for s in workflow.slots)

    sys_prompt = f"""
    You are the receptionist AI for {biz_name}.
    Your job is to collect caller details step-by-step, in this order:
      {slot_list}

    Confirm each only once, then move to the next.
    Ask politely, one question per turn.
    When appropriate, confirm understanding before proceeding.
    """

    sys_prompt += (
        "\nRespond strictly as JSON with this shape: "
        f'{{"say": "...", "tool": null, "args": {{{slot_json}}}}}'
    )

    messages: List[dict] = [
        {"role": "system", "content": sys_prompt.strip()},
        {"role": "system", "content": f"Goal: {req.goal}"},
    ]

    for m in req.messages:
        messages.append(m.to_dict())
    return messages


def _reason_with_openai(messages: list[dict]) -> Thought:
    try:
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
                            "args": {"type": ["object", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            max_tokens=200,
        )

        msg = r.choices[0].message.content
        data = msg if isinstance(msg, dict) else json.loads(msg)
        return Thought(**data)

    except Exception as e:
        print(f"[LLM ERROR] {e}")
        return Thought(say="Sorry, something went wrong.", tool=None, args=None)


# ---------------- Dialog Entry ----------------
@app.post("/dialog", response_model=Thought)
async def dialog(req: ReasonRequest, request: Request) -> Thought:
    """Central conversational endpoint."""
    call_id = request.headers.get("X-Twilio-CallSid", "local")
    caller_number = request.headers.get("X-Caller-Number", "")
    session = get_or_create_session(call_id, caller_number)
    workflow = TenantWorkflow()

    # --- First turn: greeting ---
    if not req.messages:
        session.say = workflow.opening_greeting
        session.add_message("assistant", session.say)
        print(f"[{call_id}] → Greeting issued")
        return Thought(say=session.say)

    # --- Append latest user message ---
    user_turn: Turn = req.messages[-1]
    session.add_message("user", user_turn.content)

    # --- Build prompt context for LLM ---
    messages = _build_llm_prompt_messages(workflow, req)

    # --- Call LLM to interpret input ---
    result: Thought = _reason_with_openai(messages)

    # --- Apply extracted slots from LLM ---
    if result.args:
        clean_args = {k: v for k, v in result.args.items() if isinstance(v, str) and v.strip()}
        if clean_args:
            for key, val in clean_args.items():
                session.set_slot(key, val)
            print(f"[{call_id}] → Slots updated: {clean_args}")

    # --- Next step ---
    next_slot = workflow.next_unfilled_slot(session)
    if next_slot:
        next_prompt = workflow.slot_lookup[next_slot].prompt
        print(f"[{call_id}] → Next step: {next_slot}")
        session.add_message("assistant", next_prompt)
        session.say = next_prompt
        return Thought(say=next_prompt)

    # --- All required slots filled ---
    if workflow.is_complete(session):
        workflow.on_complete(session)
        closing = (
            f"Thanks for all the details, {session.slots.get('name', 'there')}! "
            "We’ll call you back soon."
        )
        session.add_message("assistant", closing)
        session.say = closing
        return Thought(say=closing)

    # --- Fallback ---
    fallback = "I’m sorry, I didn’t catch that. Could you repeat?"
    session.add_message("assistant", fallback)
    session.say = fallback
    print(f"[{call_id}] → Fallback triggered")
    return Thought(say=fallback)


# ---------------- Health + Admin ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "tenants": tenant_mgr.list_tenants()}


@app.post("/create_lead", response_model=LeadOut)
async def create_lead(inp: LeadIn):
    lead = repo.create_lead(inp.name, inp.phone, inp.email, inp.quote_id)
    return LeadOut(lead_id=lead.lead_id)


# ---------------- Ngrok startup for local testing ----------------
from pyngrok import ngrok, conf

conf.get_default().auth_token = rt.NGROK_AUTHTOKEN

public_tunnel = ngrok.connect(8000, bind_tls=True)
public_url = public_tunnel.public_url
ws_url = public_url.replace("https", "wss")
rt.ENV.URL = public_url

print(f"[NGROK] Public URL: {public_url}")
print(f"Set this as the Twilio webhook:")
print(f"  {public_url}/twilio/voice\n")
