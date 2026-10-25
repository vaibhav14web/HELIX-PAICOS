"""
Authenticated Principal and Identity Abstractions for HELIX.

Decouples authenticated user identity from static global credentials,
enforcing server-side authorization boundaries and providing a clean foundation
for multi-user/multi-tenant progression.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """
    Represents an authenticated actor associated with a validated request.
    """
    user_id: str
    username: str
    display_name: str
    roles: tuple[str, ...] = ("user", "owner")
    is_authenticated: bool = True
    token_hash: str | None = None
    token: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        """Check if principal possesses the specified role."""
        return role in self.roles

    def is_owner(self) -> bool:
        """Check if principal is a system owner or administrator."""
        return "owner" in self.roles or "admin" in self.roles

    def is_admin(self) -> bool:
        """Check if principal is an administrator."""
        return "admin" in self.roles


def get_default_owner_principal(token_hash: str | None = None) -> AuthenticatedPrincipal:
    """
    Constructs the default authenticated owner principal for single-user desktop deployment,
    bound to the local system user profile.
    """
    from foundation.paths import get_system_username, get_system_display_name
    username = get_system_username()
    display_name = get_system_display_name()
    user_id = os.getenv("HELIX_USER_ID", f"user-{username.lower()}")
    return AuthenticatedPrincipal(
        user_id=user_id,
        username=username,
        display_name=display_name,
        roles=("user", "owner", "admin"),
        is_authenticated=True,
        token_hash=token_hash,
    )


def verify_user_authorization(principal: AuthenticatedPrincipal | None, target_user_id: str) -> bool:
    """
    Verifies that the authenticated principal is authorized to access resources belonging
    to `target_user_id`. Never trusts a client-supplied user_id without verification.
    """
    if principal is None or not principal.is_authenticated:
        return False
    if principal.is_owner():
        return True
    return principal.user_id == target_user_id
