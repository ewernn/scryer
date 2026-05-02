"""Auth endpoints: login (issue JWT), me, logout (no-op v0)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.auth import Principal, get_principal
from scryer.server.db import get_session
from scryer.server.services.errors import AuthError
from scryer.server.services.security import issue_access_jwt
from scryer.server.services.users import authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class MeResponse(BaseModel):
    id: str
    kind: str
    scopes: list[str]


@router.post(
    "/login",
    response_model=LoginResponse,
    operation_id="auth.login",
    summary="Exchange email + password for an access JWT",
)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    try:
        user = await authenticate(session, email=body.email, password=body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    await session.commit()  # persist any password rehash
    from scryer.config import get_settings

    s = get_settings()
    return LoginResponse(
        access_token=issue_access_jwt(str(user.id)),
        expires_in=s.jwt_ttl_seconds,
    )


@router.get(
    "/me",
    response_model=MeResponse,
    operation_id="auth.me",
    summary="Identity of the calling principal",
)
async def me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        id=str(principal.id),
        kind=principal.kind.value,
        scopes=sorted(s.value for s in principal.scopes),
    )
