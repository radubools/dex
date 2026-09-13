"""Google sign-in, and the dependency every route hangs its access off.

The flow is the ordinary confidential-client authorization code flow with PKCE:

    GET  /api/auth/google        -> 302 to Google
    GET  /api/auth/google/callback -> exchange, session cookie, 302 to the UI
    GET  /api/auth/me            -> who am I, and what may I see
    POST /api/auth/logout        -> end this session

Authorisation is deliberately not middleware. Middleware would have to guess a
resource's project from its URL, and most of dex's routes carry an id rather
than a slug — a thread id, a task id, an asset path. Instead each route asks for
what it needs: `Access` for "somebody signed in", `require_admin` for the admin
surface, and `access.check(project)` at the point the project is actually known.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from .config import Config
from .db import Database
from .identity import (
    ROLE_ADMIN,
    ROLES,
    SESSION_COOKIE,
    SESSION_TTL_S,
    IdentityStore,
    User,
    decode_id_token,
    pkce_pair,
)

log = logging.getLogger("dex.authn")

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


@dataclass
class Access:
    """What the current caller may do.

    `user` is None for the service credential (DEX_TOKEN) and when sign-in is
    switched off entirely — both of which mean unrestricted, because the first
    is dex's own operator and the second is the pre-auth behaviour this must not
    break for existing installs.
    """

    user: User | None
    #: True when nothing is restricted: no sign-in configured, or a valid
    #: DEX_TOKEN was presented.
    unrestricted: bool = False

    @property
    def is_admin(self) -> bool:
        return self.unrestricted or (self.user is not None and self.user.is_admin)

    def may_see(self, project: str | None) -> bool:
        if self.unrestricted:
            return True
        return self.user is not None and self.user.may_see(project)

    def check(self, project: str | None) -> None:
        """Raise unless this caller may act within `project`.

        404 rather than 403 for a project they cannot see: a 403 confirms the
        project exists, which is a small leak the admin UI has no need to
        create.
        """
        if not self.may_see(project):
            raise HTTPException(status_code=404, detail="no such project")

    def visible_projects(self, all_slugs: list[str]) -> list[str]:
        """`all_slugs` narrowed to what this caller may see."""
        if self.unrestricted or self.is_admin:
            return list(all_slugs)
        if self.user is None:
            return []
        return [s for s in all_slugs if s in self.user.projects]


def build_access_dependency(config: Config, db: Database):
    """The `Access` dependency, closed over this app's config and database."""

    async def resolve(request: Request) -> Access:
        # The service credential first: health checks and the CLI have no
        # browser, and this is also the whole story when sign-in is off.
        if config.token:
            presented = request.headers.get("x-dex-token") or request.query_params.get("t")
            if presented and presented == config.token:
                return Access(user=None, unrestricted=True)

        if not config.google_enabled:
            # No sign-in configured. If a token is set, it was required above
            # and not supplied; if not, dex is open as it has always been.
            if config.token:
                raise HTTPException(status_code=401, detail="bad or missing token")
            return Access(user=None, unrestricted=True)

        user = await IdentityStore(db).user_for_session(request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise HTTPException(status_code=401, detail="sign in required")
        if not user.authorised:
            # Signed in, no role. A distinct code so the UI can show the
            # "waiting to be authorised" page instead of bouncing them back
            # into a sign-in loop they would never escape.
            raise HTTPException(status_code=403, detail="no role assigned")
        return Access(user=user)

    return resolve


def require_admin(access: Access) -> Access:
    """Called inside a route body, where `access` is already resolved."""
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return access


def build_admin_dependency(access_dep: Any) -> Any:
    """`require_admin` as something FastAPI can actually inject.

    The plain function above has no `Depends` default, so used in a route's
    `dependencies=[...]` FastAPI reads its `access` parameter as a query
    parameter and the route stops working. This wrapper is the dependency form.
    """

    async def require(access: Access = Depends(access_dep)) -> Access:
        return require_admin(access)

    return require


def build_router(config: Config, db: Database, access_dep: Any) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    identity = IdentityStore(db)

    def _cookie(response: Response, secret: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            secret,
            max_age=SESSION_TTL_S,
            httponly=True,  # a script that leaks cannot read it
            # Lax, not Strict: the Google callback is a cross-site GET landing
            # back here, and Strict would drop the cookie we just set.
            samesite="lax",
            # Only over TLS in production. Left off for a plain-HTTP localhost,
            # where a Secure cookie would simply never be stored.
            secure=config.public_origin.startswith("https://"),
            path="/",
        )

    @router.get("/config")
    async def auth_config() -> dict[str, Any]:
        """What the sign-in page needs to render. Deliberately unauthenticated."""
        return {
            "googleEnabled": config.google_enabled,
            # True when nothing is enforced, so the UI can skip the whole
            # sign-in surface for a private install.
            "open": not config.google_enabled and not config.token,
        }

    @router.get("/google")
    async def start(next: str = Query(default="/")) -> RedirectResponse:
        if not config.google_enabled:
            raise HTTPException(status_code=404, detail="Google sign-in is not configured")
        verifier, challenge = pkce_pair()
        # Only a path, never an absolute URL: an open redirector here would let
        # someone bounce a victim through a trusted origin.
        destination = next if next.startswith("/") and not next.startswith("//") else "/"
        state = await identity.begin_oauth(verifier, destination)
        params = {
            "client_id": config.google_client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Always show the chooser: without it a shared browser silently
            # signs in whoever Google last remembered.
            "prompt": "select_account",
        }
        if config.google_hd:
            params["hd"] = config.google_hd
        return RedirectResponse(f"{GOOGLE_AUTH}?{urllib.parse.urlencode(params)}", status_code=302)

    @router.get("/google/callback")
    async def callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        request: Request = None,  # type: ignore[assignment]
    ) -> RedirectResponse:
        if error:
            log.warning("Google returned an error: %s", error)
            return RedirectResponse("/?auth_error=denied", status_code=302)
        if not code or not state:
            return RedirectResponse("/?auth_error=incomplete", status_code=302)

        started = await identity.consume_oauth(state)
        if started is None:
            # Unknown, expired, or already used. All three are the same to us.
            log.warning("callback with an unrecognised state")
            return RedirectResponse("/?auth_error=expired", status_code=302)
        verifier, destination = started

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_response = await client.post(
                    GOOGLE_TOKEN,
                    data={
                        "code": code,
                        "client_id": config.google_client_id,
                        "client_secret": config.google_client_secret,
                        "redirect_uri": config.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": verifier,
                    },
                )
            if token_response.status_code != 200:
                log.warning("token exchange failed: %s %s", token_response.status_code,
                            token_response.text[:300])
                return RedirectResponse("/?auth_error=exchange", status_code=302)
            id_token = token_response.json().get("id_token")
            if not id_token:
                return RedirectResponse("/?auth_error=no_id_token", status_code=302)
            claims = decode_id_token(id_token)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not complete sign-in: %s", exc)
            return RedirectResponse("/?auth_error=exchange", status_code=302)

        # Checked even though `hd` was sent to Google: the parameter is a UI
        # hint there, not an guarantee, so the claim is what is enforced.
        if config.google_hd and claims.get("hd") != config.google_hd:
            log.warning("rejected sign-in from outside %s", config.google_hd)
            return RedirectResponse("/?auth_error=domain", status_code=302)
        if claims.get("email_verified") is False:
            return RedirectResponse("/?auth_error=unverified", status_code=302)

        try:
            user = await identity.upsert_google_user(claims, config.admin_email_set)
        except ValueError as exc:
            log.warning("could not record this user: %s", exc)
            return RedirectResponse("/?auth_error=no_email", status_code=302)

        secret = await identity.create_session(
            user.id, request.headers.get("user-agent") if request else None
        )
        response = RedirectResponse(destination or "/", status_code=302)
        _cookie(response, secret)
        log.info("signed in %s (role=%s)", user.email, user.role or "none")
        return response

    @router.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        """Who the caller is. Never 401s — the UI uses it to decide what to show.

        A 401 here would be indistinguishable from a network failure at exactly
        the moment the UI is trying to work out whether to render a sign-in
        button, an unauthorised notice, or the app.
        """
        if config.token:
            presented = request.headers.get("x-dex-token") or request.query_params.get("t")
            if presented and presented == config.token:
                return {"state": "service", "user": None, "googleEnabled": config.google_enabled}
        if not config.google_enabled:
            return {"state": "open", "user": None, "googleEnabled": False}
        user = await identity.user_for_session(request.cookies.get(SESSION_COOKIE))
        if user is None:
            return {"state": "anonymous", "user": None, "googleEnabled": True}
        return {
            "state": "authorised" if user.authorised else "unauthorised",
            "user": user.to_json(),
            "googleEnabled": True,
        }

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        await identity.end_session(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ------------------------------------------------------------- admin ----

    @router.get("/users")
    async def list_users(access: Access = Depends(access_dep)) -> dict[str, Any]:
        require_admin(access)
        return {"users": [u.to_json() for u in await identity.list_users()]}

    @router.put("/users/{user_id}/role")
    async def set_role(
        user_id: str, body: dict[str, Any], access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        require_admin(access)
        role = body.get("role")
        if role is not None and role not in ROLES:
            raise HTTPException(status_code=422, detail=f"role must be null or one of {ROLES}")

        target = await identity.get(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no such user")
        # Locking yourself out is a one-way door on a deployment whose only
        # other way in is an env var and a restart.
        if target.is_admin and role != ROLE_ADMIN and await identity.count_admins() <= 1:
            raise HTTPException(
                status_code=409,
                detail="this is the last admin — promote someone else first",
            )
        await identity.set_role(user_id, role)
        if role is None:
            # Access removed: end their sessions rather than leaving them with a
            # cookie that now resolves to the unauthorised page.
            await identity.end_all_sessions(user_id)
        updated = await identity.get(user_id)
        return {"user": updated.to_json() if updated else None}

    @router.put("/users/{user_id}/projects")
    async def set_projects(
        user_id: str, body: dict[str, Any], access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        require_admin(access)
        target = await identity.get(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no such user")
        wanted = body.get("projects")
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            raise HTTPException(status_code=422, detail="projects must be a list of slugs")

        known = {
            r["slug"] for r in await db.pool.fetch("SELECT slug FROM projects")
        }
        unknown = [p for p in wanted if p not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown projects: {unknown}")

        actor = access.user.id if access.user else None
        if actor is None:
            # The service credential has no user row to attribute a grant to,
            # and granted_by references users(id).
            raise HTTPException(
                status_code=403, detail="grants must be made by a signed-in admin"
            )
        await identity.set_projects(user_id, sorted(set(wanted)), actor)
        updated = await identity.get(user_id)
        return {"user": updated.to_json() if updated else None}

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, access: Access = Depends(access_dep)) -> Response:
        require_admin(access)
        target = await identity.get(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no such user")
        if target.is_admin and await identity.count_admins() <= 1:
            raise HTTPException(status_code=409, detail="this is the last admin")
        if access.user and access.user.id == user_id:
            raise HTTPException(status_code=409, detail="you cannot delete yourself")
        await identity.delete_user(user_id)
        return Response(status_code=204)

    return router
