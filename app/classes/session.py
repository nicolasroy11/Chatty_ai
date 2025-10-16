from typing import Any
from app.classes.turn import Turn

class SessionState:
    """Universal conversational state shared across all tenants."""

    def __init__(self, call_id: str, caller_number: str = ""):
        self.call_id = call_id
        self.caller_number = caller_number
        self.slots: dict[str, Any] = {}
        self.messages: list[Turn] = []
        self.say: str | None = None
        self.step_index: int = 0

    def add_message(self, role: str, content: str) -> None:
        """Append a user or assistant turn."""
        self.messages.append(Turn(role=role, content=content))

    def set_slot(self, key: str, value: Any) -> None:
        """Safely set or update a slot value (skip empty / meaningless)."""
        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        self.slots[key] = value

    def get_slot(self, key: str, default: Any = None) -> Any:
        return self.slots.get(key, default)

    def all_required_filled(self, required_slots: list[str]) -> bool:
        """Check if all required slots are present and non-empty."""
        for k in required_slots:
            v = self.slots.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                return False
        return True

    def summary(self) -> str:
        lines = [f"{k.capitalize()}: {v}" for k, v in self.slots.items() if v is not None]
        return "\n".join(lines) if lines else "(no details collected yet)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "caller_number": self.caller_number,
            "slots": self.slots,
            "messages": [t.to_dict() for t in self.messages],
            "say": self.say,
            "step_index": self.step_index,
        }
