from __future__ import annotations
from typing import Dict
from dataclasses import dataclass
import uuid

@dataclass
class Lead:
    lead_id: uuid.UUID
    name: str
    phone: str
    email: str | None
    quote_id: uuid.UUID | None

@dataclass
class Order:
    order_id: uuid.UUID
    quote_id: uuid.UUID

class Repo:
    def __init__(self):
        self.leads: Dict[uuid.UUID, Lead] = {}
        self.orders: Dict[uuid.UUID, Order] = {}

    def create_lead(self, name: str, phone: str, email: str | None, quote_id: uuid.UUID | None) -> Lead:
        lid = uuid.uuid4()
        lead = Lead(lid, name, phone, email, quote_id)
        self.leads[lid] = lead
        return lead

    def create_order(self, quote_id: uuid.UUID) -> Order:
        oid = uuid.uuid4()
        order = Order(oid, quote_id)
        self.orders[oid] = order
        return order

repo = Repo()
