"""Fixed organization capabilities; evaluated against the live account on each request."""

from fastapi import Depends, HTTPException

OPERATIONAL_ROLES = {"CLIENT_PIC", "INTAKE_SUPPORT", "FINANCE_REVIEWER", "SERVICE_LEAD"}
ADMIN_ROLES = {"SUPER_ADMIN", "SYSTEM_ADMIN"}
ROLE_PERMISSIONS = {
    "CLIENT_PIC": {"upload", "refine", "submit", "voucher", "history", "chat"},
    "INTAKE_SUPPORT": {"upload", "chat"},
    "FINANCE_REVIEWER": {"review", "approve", "voucher", "history", "chat"},
    "SERVICE_LEAD": {"allocate", "history", "metrics", "chat"},
    "SYSTEM_ADMIN": {"clients", "templates", "users", "assignments", "history", "metrics", "chat"},
}
for _role in ("CLIENT_PIC", "INTAKE_SUPPORT", "FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN"):
    ROLE_PERMISSIONS[_role].add("track")
ALL_PERMISSIONS = set().union(*ROLE_PERMISSIONS.values()) | {"administrators"}


def permissions(user):
    return sorted(ALL_PERMISSIONS if user.role.value == "SUPER_ADMIN" else ROLE_PERMISSIONS.get(user.role.value, set()))


def require_permission(permission):
    from app.dependencies import get_current_user

    async def check(user=Depends(get_current_user)):
        if permission not in permissions(user):
            raise HTTPException(403, "You do not have authority to perform this action.")
        return user

    return check
