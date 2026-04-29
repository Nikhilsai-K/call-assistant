from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    clerk_org_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False, default="starter")
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hipaa_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pci_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recording_retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    twilio_subaccount_sid: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
