# app/schemas.py
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import uuid
from app.classes.turn import Turn


# ---------- Core Reasoning Schemas ----------

@dataclass
class ReasonRequest:
    messages: List[Turn]
    goal: str = "Produce a brief, helpful reply and any next tool as JSON."


@dataclass
class Thought:
    say: str
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- Quote & Pricing ----------

@dataclass
class QuoteItemIn:
    id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    qty: int = 1


@dataclass
class QuoteIn:
    date: str
    zip: str
    items: List[QuoteItemIn]


@dataclass
class MoneyOut:
    line_items: List[Dict[str, Any]]
    subtotal: float
    delivery_fee: float
    labor_fee: float
    discounts: float
    tax: float
    total: float


# ---------- Availability ----------

@dataclass
class AvailabilityIn:
    date: str
    items: List[QuoteItemIn]


@dataclass
class AvailabilityOut:
    available: bool
    shortages: List[Dict[str, Any]] = field(default_factory=list)
    substitutions: List[Dict[str, Any]] = field(default_factory=list)


# ---------- Lead Management ----------

@dataclass
class LeadIn:
    name: str
    phone: str
    email: Optional[str] = None
    quote_id: Optional[uuid.UUID] = None


@dataclass
class LeadOut:
    lead_id: uuid.UUID


# ---------- Booking ----------

@dataclass
class BookIn:
    quote_id: uuid.UUID
    payment_token: str


@dataclass
class BookOut:
    order_id: uuid.UUID


# ---------- Admin / Catalog ----------

@dataclass
class ItemDef:
    id: uuid.UUID
    name: str
    daily_price: float
    qty: int = 0


@dataclass
class ItemCreate:
    name: str
    daily_price: float
    qty: int = 0


@dataclass
class ItemUpdate:
    name: Optional[str] = None
    daily_price: Optional[float] = None
    qty: Optional[int] = None
