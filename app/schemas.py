from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
import uuid

class Turn(BaseModel):
    role: Literal["system","user","assistant"]
    content: str

class ReasonRequest(BaseModel):
    messages: List[Turn]
    goal: str = Field("Produce a brief, helpful reply and any next tool as JSON.")

class Thought(BaseModel):
    say: str
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

class QuoteItemIn(BaseModel):
    id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    qty: int = 1

class QuoteIn(BaseModel):
    date: str
    zip: str
    items: List[QuoteItemIn]

class MoneyOut(BaseModel):
    line_items: List[Dict[str, Any]]
    subtotal: float
    delivery_fee: float
    labor_fee: float
    discounts: float
    tax: float
    total: float

class AvailabilityIn(BaseModel):
    date: str
    items: List[QuoteItemIn]

class AvailabilityOut(BaseModel):
    available: bool
    shortages: List[Dict[str, Any]] = []
    substitutions: List[Dict[str, Any]] = []

class LeadIn(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    quote_id: Optional[uuid.UUID] = None

class LeadOut(BaseModel):
    lead_id: uuid.UUID

class BookIn(BaseModel):
    quote_id: uuid.UUID
    payment_token: str

class BookOut(BaseModel):
    order_id: uuid.UUID

# Admin catalog models
class ItemDef(BaseModel):
    id: uuid.UUID
    name: str
    daily_price: float
    qty: int = 0

class ItemCreate(BaseModel):
    name: str
    daily_price: float
    qty: int = 0

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    daily_price: Optional[float] = None
    qty: Optional[int] = None
