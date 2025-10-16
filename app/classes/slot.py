from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Slot:
    name: str
    prompt: str
    description: str
    required: bool = True
    example: Optional[str] = None