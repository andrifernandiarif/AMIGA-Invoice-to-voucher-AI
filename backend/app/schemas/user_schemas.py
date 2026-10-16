# backend/app/schemas/user_schemas.py

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole


class AssignmentIn(BaseModel):
    client_id: uuid.UUID


class CurrentRoleRequest(BaseModel):
    role: Literal[
        UserRole.SUPER_ADMIN,
        UserRole.SYSTEM_ADMIN,
        UserRole.CLIENT_PIC,
        UserRole.INTAKE_SUPPORT,
        UserRole.FINANCE_REVIEWER,
        UserRole.SERVICE_LEAD,
    ]

    @field_validator("role", mode="before")
    @classmethod
    def current_role(cls, value):
        if value == UserRole.USER_ADMIN:
            raise ValueError("Use SYSTEM_ADMIN; USER_ADMIN has been retired.")
        return value


class CreateUserRequest(CurrentRoleRequest):
    username: str = Field(..., min_length=1, max_length=200)
    full_name: str = Field(min_length=1, max_length=300)
    job_title: str = Field(min_length=1, max_length=200)
    temporary_password: str = Field(min_length=12, max_length=128)
    signature_data: str | None = Field(default=None, max_length=3000000)
    assignments: list[AssignmentIn] = Field(default_factory=list)


class UpdateRoleRequest(CurrentRoleRequest):
    pass


class AddAssignmentRequest(BaseModel):
    client_id: uuid.UUID


class UserResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    role: UserRole
    full_name: str | None = None
    job_title: str | None = None
    is_active: bool
    must_change_password: bool = False
    permissions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssignmentResponse(BaseModel):
    assignment_id: uuid.UUID
    user_id: uuid.UUID
    client_id: uuid.UUID
    is_active: bool

    model_config = {"from_attributes": True}
