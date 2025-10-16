from __future__ import annotations
from typing import List, Dict, Optional
from app.classes.slot import Slot
from app.utils.mailgun_client import send_email
from app.utils.extractors import extract_slot_from_text
from app.classes.session import SessionState
import runtime_settings as rt


class TenantWorkflow:
    """Defines the conversational logic for this specific tenant."""

    def __init__(self) -> None:
        self.tenant_name: str = rt.TENANT.NAME
        self.opening_greeting: str = rt.TENANT.OPENING_GREETING

        # Declare the slots directly here
        self.slots: List[Slot] = [
            Slot("name", "Who am I speaking with?", "The caller’s name."),
            Slot("phone", "Can I get your phone number please?", "The best number to reach the caller."),
            Slot("date", "What date is your event?", "The event date."),
            Slot("zip", "What is the zipcode of your event?", "The zip of the event."),
            Slot("notes", "Is there anything else you’d like us to know?", "Extra details or requests.", required=False),
        ]

        self.slot_lookup: Dict[str, Slot] = {s.name: s for s in self.slots}
        self.required_slots: List[str] = [s.name for s in self.slots if s.required]


    # CORE HANDLER - returns the next text the assistant should say
    def handle_step(self, session: SessionState, user_text: str) -> str:
        """Handle a user utterance and advance the dialog."""
        next_slot = self.next_unfilled_slot(session)
        if next_slot is None:
            self.on_complete(session)
            return f"Thanks {session.slots.get('name', '')}, your details have been received."

        # Try to extract the slot value
        slot = self.slot_lookup[next_slot]
        value = extract_slot_from_text(slot, user_text)
        if value:
            session.set_slot(next_slot, value)
            next_slot = self.next_unfilled_slot(session)
            if next_slot is None:
                self.on_complete(session)
                return f"Thank you, {session.slots.get('name', '')}! We’ve collected everything we need."

        # If still missing, ask the prompt associated with this slot
        return self.slot_lookup[next_slot].prompt

    def next_unfilled_slot(self, session: SessionState) -> Optional[str]:
        for s in self.required_slots:
            if not session.slots.get(s):
                return s
        return None

    def is_complete(self, session: SessionState) -> bool:
        return all(session.slots.get(s) for s in self.required_slots)


    # HOOKS

    def on_complete(self, session: SessionState) -> None:
        subject = f"New lead for {self.tenant_name}"
        lines = [f"Caller: {session.caller_number or '(unknown)'}", "", "Collected details:"]
        for k, v in session.slots.items():
            lines.append(f"- {k}: {v if v else '(missing)'}")

        body = "\n".join(lines)
        ok = send_email(to=rt.ENV.NOTIFICATIONS_EMAIL, subject=subject, text=body)

        if ok:
            print(f"[EMAIL SENT] Lead summary for {session.caller_number} delivered to {rt.ENV.NOTIFICATIONS_EMAIL}")
        else:
            print(f"[EMAIL FAILED] Could not send lead summary for {session.call_id}")
            print(body)
