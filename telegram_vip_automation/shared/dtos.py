from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class PackageResponse(BaseModel):
    id: int
    name: str
    description: str
    price: float
    duration_days: int

    class Config:
        from_attributes = True


class PaymentCreate(BaseModel):
    telegram_id: int
    package_id: int
    idempotency_key: str
    username: Optional[str] = ""
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""


class PaymentResponse(BaseModel):
    id: int
    user_id: int
    package_id: int
    amount: float
    currency: str
    status: str
    provider_tx_id: Optional[str] = None
    idempotency_key: str

    class Config:
        from_attributes = True


class WebhookPayload(BaseModel):
    provider_tx_id: str


class SubscriptionInfo(BaseModel):
    id: int
    start_date: datetime
    end_date: datetime
    package_name: str
    is_active: bool


class SubscriptionStatusResponse(BaseModel):
    telegram_id: int
    has_active_subscription: bool
    subscription: Optional[SubscriptionInfo] = None


class EventCreate(BaseModel):
    event_type: str
    user_id: Optional[int] = None
    payload_json: dict = Field(default_factory=dict)
    correlation_id: str
    source_service: str
