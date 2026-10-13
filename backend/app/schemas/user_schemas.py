# backend/app/schemas/user_schemas.py

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import UserRole


class AssignmentIn(BaseModel):
    client_id: uuid.UUID


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    role: UserRole
    assignments: list[AssignmentIn] = Field(..., min_length=1)


class UpdateRoleRequest(BaseModel):
    role: UserRole


class AddAssignmentRequest(BaseModel):
    client_id: uuid.UUID


class UserResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssignmentResponse(BaseModel):
    assignment_id: uuid.UUID
    user_id: uuid.UUID
    client_id: uuid.UUID
    is_active: bool

    model_config = {"from_attributes": True}
