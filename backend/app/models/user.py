import enum
import uuid
from datetime import datetime

from sqlalchemy import Integer, Boolean, DateTime, Enum as SAEnum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    USER_ADMIN = "USER_ADMIN"
    CLIENT_PIC = "CLIENT_PIC"
    INTAKE_SUPPORT = "INTAKE_SUPPORT"
    FINANCE_REVIEWER = "FINANCE_REVIEWER"
    SERVICE_LEAD = "SERVICE_LEAD"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signature_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, name="userrole"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
