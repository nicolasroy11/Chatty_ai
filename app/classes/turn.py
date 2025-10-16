from dataclasses import dataclass

@dataclass
class Turn:
    role: str      # "user" or "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        """Convert to OpenAI-style message dict."""
        return {"role": self.role, "content": self.content}