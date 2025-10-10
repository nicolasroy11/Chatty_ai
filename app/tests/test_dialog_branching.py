import app.main as main

# Greeting: first call from a new user
def test_first_turn_greeting(client):
    """Bot should greet with a fixed, friendly intro asking for the caller's name."""
    lead_payload = {"goal": "lead", "messages": []}
    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert response.status_code == 200
    assert response_data["say"] == main.OPENING_GREETING
    assert response_data["tool"] is None


# Sequential slot filling: name → phone → date → city
def test_piecemeal_slot_collection_with_phone(monkeypatch, client):
    """
    The bot should collect info step by step:
    1. name → confirms phone number from call metadata
    2. phone → asks for event date
    3. date → asks for city
    4. city → offers to list items or get a callback
    """

    # --- Step 1: user gives name ---
    fake_step1 = {
        "say": "Thanks, Alice — is this still the best number to reach you?",
        "tool": None,
        "args": {"name": "Alice", "phone": "8185551234"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_step1))
    payload1 = {
        "goal": "lead",
        "messages": [{"role": "user", "content": "Hi, this is Alice."}],
    }
    resp1 = client.post(
        "/dialog",
        json=payload1,
        headers={"X-Tenant": "special-events", "X-Caller-Number": "8185551234"},
    )
    data1 = resp1.json()
    assert "best number" in data1["say"].lower()
    assert data1["args"]["name"] == "Alice"

    # --- Step 2: user confirms number ---
    fake_step2 = {
        "say": "Perfect. When is your event?",
        "tool": None,
        "args": {"name": "Alice", "phone": "8185551234"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_step2))
    payload2 = {
        "goal": "lead",
        "messages": [{"role": "user", "content": "Yep that is right."}],
    }
    resp2 = client.post("/dialog", json=payload2, headers={"X-Tenant": "special-events"})
    data2 = resp2.json()
    assert "when is your event" in data2["say"].lower()

    # --- Step 3: user gives date ---
    fake_step3 = {
        "say": "And what city will that be in?",
        "tool": None,
        "args": {"name": "Alice", "phone": "8185551234", "date": "2025-05-15"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_step3))
    payload3 = {"goal": "lead", "messages": [{"role": "user", "content": "It’s May 15th."}]}
    resp3 = client.post("/dialog", json=payload3, headers={"X-Tenant": "special-events"})
    data3 = resp3.json()
    assert "what city" in data3["say"].lower()

    # --- Step 4: user gives city ---
    fake_step4 = {
        "say": "Got it — would you like to list items or have us call you back?",
        "tool": None,
        "args": {"name": "Alice", "phone": "8185551234", "date": "2025-05-15", "city": "Woodland Hills"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_step4))
    payload4 = {"goal": "lead", "messages": [{"role": "user", "content": "In Woodland Hills."}]}
    resp4 = client.post("/dialog", json=payload4, headers={"X-Tenant": "special-events"})
    data4 = resp4.json()
    assert any(k in data4["say"].lower() for k in ["list items", "call you back"])


# Callback branch → create_lead and email
def test_callback_branch_creates_lead_and_emails(monkeypatch, client):
    """If caller chooses callback, the bot should create a lead and send an email."""
    fake_response = {
        "say": "Perfect, we’ll call you back shortly.",
        "tool": "create_lead",
        "args": {"name": "Alice", "phone": "555-1234", "date": "2025-05-15", "city": "Woodland Hills"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    sent_email = {}
    monkeypatch.setattr(main, "send_lead_email", lambda subject, body: sent_email.update(subject=subject, body=body))

    lead_payload = {"goal": "lead", "messages": [{"role": "user", "content": "Call me back."}]}
    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert response.status_code == 200
    assert response_data["tool"] == "create_lead"


# Caller lists items → capture item list and still email owner
def test_list_items_branch_then_email(monkeypatch, client):
    """If the user wants to list items, capture them and still create a lead."""
    fake_response = {
        "say": "Got it, we have your list and will call you back.",
        "tool": "create_lead",
        "args": {
            "name": "Alice",
            "phone": "555-1234",
            "date": "2025-05-15",
            "city": "Woodland Hills",
            "items": [
                {"name": "Resin Folding Chair (White)", "qty": 50},
                {"name": 'Table 60" Round', "qty": 5},
            ],
        },
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {
        "goal": "lead",
        "messages": [{"role": "user", "content": "Let's list: 50 white resin chairs, five 60 inch rounds."}],
    }

    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert response.status_code == 200
    assert response_data["tool"] == "create_lead"
    assert len(response_data["args"]["items"]) == 2


# Ambiguous item → bot should clarify before proceeding
def test_ambiguous_item_triggers_clarification(monkeypatch, client):
    """
    If the user says something vague like '30 tables',
    the bot should ask which kind of table instead of assuming.
    """
    fake_response = {
        "say": "We have several kinds of tables — round or rectangular?",
        "tool": None,
        "args": {"items": [{"name": "tables", "qty": 30}]},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {"goal": "lead", "messages": [{"role": "user", "content": "I need 30 tables"}]}
    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert response.status_code == 200
    assert any(keyword in response_data["say"].lower() for keyword in ["round", "rectangular"])
    assert response_data["tool"] is None


# Fallback: if something fails, still email with partial info
def test_incomplete_info_triggers_fallback_email(monkeypatch, client):
    """Even when reasoning fails, the system should still send an 'incomplete lead' email."""
    sent_email = {}
    monkeypatch.setattr(main, "send_lead_email", lambda subject, body: sent_email.update(subject=subject, body=body))

    # proper mock that raises directly (not generator)
    def raise_reason_error(_):
        raise Exception("LLM timeout")

    monkeypatch.setattr(main, "_reason_with_openai", raise_reason_error)

    # IMPORTANT: include a user message so we DON'T hit the greeting path
    response = client.post(
        "/dialog",
        json={"goal": "lead", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Tenant": "special-events", "X-Caller-Number": "+18185551234"},
    )

    assert response.status_code == 500
    assert "incomplete" in sent_email["subject"].lower()
    assert "818" in sent_email["body"]


# Confirm phone number from call metadata
def test_bot_confirms_number_from_metadata(monkeypatch, client):
    """Bot should confirm the caller’s number from metadata before continuing."""
    fake_response = {
        "say": "I see your number is (818) 555-1234. Is that the best number to reach you?",
        "tool": None,
        "args": {"phone": "8185551234"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {
        "goal": "lead",
        "messages": [{"role": "user", "content": "My event is May 15 in Woodland Hills"}],
    }

    response = client.post(
        "/dialog",
        json=lead_payload,
        headers={"X-Tenant": "special-events", "X-Caller-Number": "8185551234"},
    )
    response_data = response.json()

    assert "best number" in response_data["say"].lower()
    assert response_data["args"]["phone"] == "8185551234"


# Caller corrects number → store new one
def test_user_corrects_number(monkeypatch, client):
    """If caller provides a new number, the bot should acknowledge and update it."""
    fake_response = {
        "say": "Thanks! I’ve updated your number to (323) 555-0000.",
        "tool": None,
        "args": {"phone": "3235550000"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {"goal": "lead", "messages": [{"role": "user", "content": "Actually, call me at 323-555-0000"}]}
    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert "updated" in response_data["say"].lower()
    assert response_data["args"]["phone"].endswith("0000")


# Missing slot (city) → bot should ask specifically for it
def test_missing_city_prompts_followup(monkeypatch, client):
    """If city is missing, bot should explicitly ask for it."""
    fake_response = {"say": "What city will your event be in?", "tool": None, "args": {}}
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {"goal": "lead", "messages": [{"role": "user", "content": "My name is Alice, May 15"}]}
    response = client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})
    response_data = response.json()

    assert "what city" in response_data["say"].lower()


# Verify that outgoing email body includes all filled slots
def test_email_contains_all_filled_slots(monkeypatch, client):
    """The generated email body should contain all filled fields from the conversation."""
    sent_email = {}
    monkeypatch.setattr(main, "send_lead_email", lambda subject, body: sent_email.update(subject=subject, body=body))
    fake_response = {
        "say": "Perfect, we’ll call you soon.",
        "tool": "create_lead",
        "args": {"name": "Alice", "phone": "8185551234", "date": "2025-05-15", "city": "Woodland Hills"},
    }
    monkeypatch.setattr(main, "_reason_with_openai", lambda _: main.Thought(**fake_response))

    lead_payload = {"goal": "lead", "messages": [{"role": "user", "content": "Call me back"}]}
    client.post("/dialog", json=lead_payload, headers={"X-Tenant": "special-events"})

    email_body = sent_email["body"]
    for field_value in ["Alice", "Woodland Hills", "818", "2025"]:
        assert field_value in email_body


# Caller hangs up early → still send minimal email
def test_hangup_partial_info_still_emails(monkeypatch, client):
    """If caller hangs up before finishing, send minimal 'incomplete' lead email."""
    sent_email = {}
    monkeypatch.setattr(main, "send_lead_email", lambda subject, body: sent_email.update(subject=subject, body=body))

    def raise_hangup_error(_):
        raise Exception("caller hung up")

    monkeypatch.setattr(main, "_reason_with_openai", raise_hangup_error)

    # IMPORTANT: include a user message so we DON'T hit the greeting path
    response = client.post(
        "/dialog",
        json={"goal": "lead", "messages": [{"role": "user", "content": "hello"}]},
        headers={"X-Tenant": "special-events", "X-Caller-Number": "8185551234"},
    )

    assert response.status_code == 500
    assert "818" in sent_email["body"]
    assert "incomplete" in sent_email["subject"].lower()


def test_full_natural_lead_flow_with_slot_tracking(monkeypatch, client):
    """
    Realistic multi-turn conversation verifying both dialog flow and slot filling.
    """

    # track each LLM response (mocked)
    scripted_responses = [
        # after name
        {
            "say": "Hi Nick, is 8187776543 the right number to reach you?",
            "tool": None,
            "args": {"name": "Nick"}
        },
        # after phone confirmed
        {
            "say": "Thanks for that. What date is your event?",
            "tool": None,
            "args": {"name": "Nick", "phone": "8187776543"}
        },
        # after date
        {
            "say": "Will you be needing delivery or will you pick up?",
            "tool": None,
            "args": {"name": "Nick", "phone": "8187776543", "date": "next Saturday, the 11th"}
        },
        # after delivery mode
        {
            "say": "Would you like to list the items you’ll be needing or do you want us to contact you?",
            "tool": None,
            "args": {
                "name": "Nick",
                "phone": "8187776543",
                "date": "next Saturday, the 11th",
                "delivery": True
            }
        },
        # after deciding to list
        {
            "say": "Ok, go ahead.",
            "tool": None,
            "args": {
                "name": "Nick",
                "phone": "8187776543",
                "date": "next Saturday, the 11th",
                "delivery": True,
                "listing": True
            }
        },
        # after giving items
        {
            "say": "Got it — we’ll pass this list to the team and call you back shortly.",
            "tool": "create_lead",
            "args": {
                "name": "Nick",
                "phone": "8187776543",
                "date": "next Saturday, the 11th",
                "delivery": True,
                "items": [
                    {"name": "chairs", "qty": 50},
                    {"name": "tables", "qty": 10},
                    {"name": "tent", "qty": 1}
                ]
            }
        }
    ]

    # mock LLM call to step through the above sequence
    turn_index = {"i": 0}
    def fake_reason(_):
        thought = main.Thought(**scripted_responses[turn_index["i"]])
        turn_index["i"] += 1
        return thought

    monkeypatch.setattr(main, "_reason_with_openai", fake_reason)

    # sequential user messages
    user_messages = [
        "Nick",
        "yep",
        "next saturday, the 11th I believe",
        "delivery",
        "I can tell you what I need",
        "ok so I need about 50 chairs, 10 tables and a tent that can fit all that",
    ]

    collected_args = []

    # run the simulated flow
    for msg in user_messages:
        payload = {"goal": "lead", "messages": [{"role": "user", "content": msg}]}
        resp = client.post(
            "/dialog",
            json=payload,
            headers={"X-Tenant": "special-events", "X-Caller-Number": "8187776543"}
        )
        data = resp.json()
        collected_args.append(data.get("args") or {})

        assert resp.status_code == 200
        assert data["say"]

    # --- verify slot progression ---
    # after each stage, more info should be present
    assert "name" in collected_args[0]
    assert "phone" in collected_args[1]
    assert "date" in collected_args[2]
    assert "delivery" in collected_args[3]
    assert "listing" in collected_args[4]
    assert "items" in collected_args[-1]
    assert collected_args[-1]["items"][0]["name"] == "chairs"

    # --- final step triggers lead creation ---
    assert data["tool"] == "create_lead"
