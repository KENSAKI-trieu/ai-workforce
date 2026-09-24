"""
Auth API routes.

The access token is returned in the response body and held by the client; the refresh
token is returned *only* as an HttpOnly cookie and never appears in a body, so page
script has no way to read or forward it.
"""

from fastapi import APIRouter, Depends, Response, Request, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.schemas.auth import (
    ChangePasswordRequest,
    RegisterRequest,
    LoginRequest,
    LoginResponse,
)
from app.domains.platform.auth_service import (
    change_password,
    login_user,
    logout_user,
    refresh_tokens,
    register_user,
)
from app.domains.platform.login_rate_limit import (
    clear_login_rate_limit,
    enforce_login_rate_limit,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


from app.core.config import settings


# The cookie is scoped to the auth routes: nothing outside this router reads it, and a
# credential that is not attached to every request cannot leak through one.
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _cookie_attributes() -> dict:
    """The attribute set shared by set and delete.

    delete_cookie must repeat path, domain, secure and samesite exactly, or the browser
    treats it as a different cookie and keeps the old one -- a logout that logs nobody out.
    """
    return {
        "path": REFRESH_COOKIE_PATH,
        "domain": settings.COOKIE_DOMAIN,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "httponly": True,
    }


def set_refresh_cookie(response: Response, refresh_token_str: str):
    """Store the refresh token strictly in an HttpOnly cookie to prevent XSS theft.

    This is now literally true: the token is no longer echoed in the response body, so
    JavaScript on the page has no way to read it.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token_str,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        **_cookie_attributes(),
    )


def clear_refresh_cookie(response: Response):
    response.delete_cookie(key=REFRESH_COOKIE_NAME, **_cookie_attributes())


@router.post("/register", response_model=LoginResponse, status_code=201, summary="Register new user")
def register(
    data: RegisterRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    # Registration is public and each call creates a tenant with its departments and
    # seven agents, so it is metered on the same budget as login.
    enforce_login_rate_limit(request, data.email)
    session = register_user(db, data, request=request)
    set_refresh_cookie(response, session.refresh_token)
    return session.response


@router.post("/login", response_model=LoginResponse, summary="Login and receive an access token")
def login(
    data: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    enforce_login_rate_limit(request, data.email)
    session = login_user(db, data, request=request)
    # Only a successful login clears the counter, so a wrong password still costs the
    # attacker part of the window while a legitimate typo costs the user nothing.
    clear_login_rate_limit(data.email)
    set_refresh_cookie(response, session.refresh_token)
    return session.response


@router.post("/refresh", response_model=LoginResponse, summary="Rotate the session using the HttpOnly cookie")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    # Cookie only. The old JSON-body fallback existed so the frontend could send a token
    # it kept in localStorage -- which is exactly the storage this design is meant to
    # avoid. Accepting the body would keep that path alive for anyone who stole one.
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token cookie is missing",
        )

    session = refresh_tokens(db, refresh_token, request=request)
    set_refresh_cookie(response, session.refresh_token)
    return session.response


@router.post(
    "/change-password",
    response_model=LoginResponse,
    summary="Change the current user's password and revoke every other session",
)
def change_password_route(
    data: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
) -> LoginResponse:
    session = change_password(
        db,
        current_user,
        data.current_password,
        data.new_password,
        request=request,
    )
    set_refresh_cookie(response, session.refresh_token)
    return session.response


@router.post("/logout", summary="Revoke the session and clear the HttpOnly refresh cookie")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    # Clearing the cookie is not logging out: the token itself stays valid until it
    # expires unless the server is told to revoke it.
    logout_user(db, request.cookies.get(REFRESH_COOKIE_NAME))
    clear_refresh_cookie(response)
    return {"message": "Logged out successfully"}
