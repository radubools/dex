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
from fastapi.responses import JSONResponse, RedirectResponse

from .config import Config
from .db import Database
from .identity import (
    CAP_MANAGE_USERS,
    MIN_PASSWORD_LENGTH,
    ROLE_ADMIN,
    ROLE_DESCRIPTIONS,
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

    def can(self, capability: str) -> bool:
        """Whether this caller may perform `capability` anywhere at all.

        The `what`, with no opinion on the `where` — pair it with `check()`
        wherever the project is known. Kept separate because most routes learn
        their project inside the body, from a task id or a request field, long
        after the dependency has run.
        """
        if self.unrestricted:
            return True
        return self.user is not None and self.user.can(capability)

    def require(self, capability: str) -> None:
        """Raise 403 unless this caller has `capability`.

        403 rather than the 404 `check` uses: this is not about hiding whether
        something exists, it is telling a signed-in person that their role does
        not cover this. The UI turns it into a sentence.
        """
        if not self.can(capability):
            raise HTTPException(
                status_code=403,
                detail=f"your role does not allow this ({capability})",
            )

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

        if not config.auth_enabled:
            # No sign-in configured, by either mechanism. If a token is set, it
            # was required above and not supplied; if not, dex is open as it
            # has always been.
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
        if user.must_change_password:
            # A password somebody else chose. The only thing this session may
            # do is replace it, and that route does not use this dependency.
            raise HTTPException(status_code=428, detail="password change required")
        return Access(user=user)

    return resolve


def require_admin(access: Access) -> Access:
    """Called inside a route body, where `access` is already resolved."""
    access.require(CAP_MANAGE_USERS)
    return access


def build_capability_dependency(access_dep: Any, capability: str) -> Any:
    """A dependency that admits only callers holding `capability`.

    Written as a factory because a bare function with no `Depends` default,
    used in a route's `dependencies=[...]`, has its `access` parameter read by
    FastAPI as a *query* parameter — which silently breaks the route rather
    than failing loudly.
    """

    async def require(access: Access = Depends(access_dep)) -> Access:
        access.require(capability)
        return access

    return require


def build_admin_dependency(access_dep: Any) -> Any:
    """Admin-only, in dependency form."""
    return build_capability_dependency(access_dep, CAP_MANAGE_USERS)


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

    async def _session_user(request: Request) -> User | None:
        return await identity.user_for_session(request.cookies.get(SESSION_COOKIE))

    async def _apply_projects(user_id: str, wanted: Any, actor: str) -> None:
        """Validate a set of project grants and replace this user's with it.

        Shared by "create a user with these projects" and "change this user's
        projects", so a slug is checked against the projects table in both --
        `user_projects.project` is a foreign key, and an unchecked bad slug
        surfaces as a 500 rather than as the mistake it is.
        """
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            raise HTTPException(status_code=422, detail="projects must be a list of slugs")
        known = {r["slug"] for r in await db.pool.fetch("SELECT slug FROM projects")}
        unknown = [p for p in wanted if p not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown projects: {unknown}")
        await identity.set_projects(user_id, sorted(set(wanted)), actor)

    @router.get("/config")
    async def auth_config() -> dict[str, Any]:
        """What the sign-in page needs to render. Deliberately unauthenticated."""
        return {
            "googleEnabled": config.google_enabled,
            "passwordEnabled": config.password_auth,
            # True when nothing is enforced, so the UI can skip the whole
            # sign-in surface for a private install.
            "open": not config.auth_enabled and not config.token,
            "roles": [{"role": r, "description": ROLE_DESCRIPTIONS[r]} for r in ROLES],
            "minPasswordLength": MIN_PASSWORD_LENGTH,
        }

    # --------------------------------------------------- password sign-in ----

    @router.post("/login")
    async def login(body: dict[str, Any], request: Request) -> Response:
        """Sign in with a username and a password.

        One 401 for every failure. Distinguishing "no such user" from "wrong
        password" turns this into a way to enumerate accounts.
        """
        if not config.password_auth:
            raise HTTPException(status_code=404, detail="password sign-in is not enabled")
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not username or not password:
            raise HTTPException(status_code=422, detail="username and password are required")

        user = await identity.authenticate(username, password)
        if user is None:
            raise HTTPException(status_code=401, detail="incorrect username or password")

        secret = await identity.create_session(user.id, request.headers.get("user-agent"))
        # The body carries the user so the UI can route immediately -- to the
        # app, to the unauthorised page, or to the password change -- without a
        # second round trip that would race the cookie being stored.
        response = JSONResponse({"user": user.to_json()})
        _cookie(response, secret)
        log.info("signed in %s (role=%s)", user.username or user.email, user.role or "none")
        return response

    @router.post("/password")
    async def change_own_password(body: dict[str, Any], request: Request) -> Response:
        """Change my own password.

        Deliberately does not use the access dependency: this is the one thing
        an account with `must_change_password` set is allowed to do, and that
        dependency rejects those with a 428.
        """
        user = await _session_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="sign in required")

        current = str(body.get("current") or "")
        new = str(body.get("new") or "")
        # Proving they know the current one is what stops a stolen session from
        # being turned into a permanent password. Skipped only when the current
        # password is the one an admin just handed over, which the holder of
        # this session has already demonstrated by signing in with it.
        if not user.must_change_password:
            confirmed = await identity.authenticate(user.username or "", current)
            if confirmed is None or confirmed.id != user.id:
                raise HTTPException(status_code=403, detail="that is not your current password")
        if len(new) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"a password must be at least {MIN_PASSWORD_LENGTH} characters",
            )
        if new == current:
            raise HTTPException(status_code=422, detail="that is the password you already have")

        # `set_password` ends every session, this one included, so a new one is
        # issued here -- otherwise changing a password signs you out of the tab
        # you changed it in.
        await identity.set_password(user.id, new, must_change=False)
        secret = await identity.create_session(user.id, request.headers.get("user-agent"))
        updated = await identity.get(user.id)
        response = JSONResponse({"user": updated.to_json() if updated else None})
        _cookie(response, secret)
        log.info("password changed for %s", user.username or user.email)
        return response

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
        how = {
            "googleEnabled": config.google_enabled,
            "passwordEnabled": config.password_auth,
        }
        if config.token:
            presented = request.headers.get("x-dex-token") or request.query_params.get("t")
            if presented and presented == config.token:
                return {"state": "service", "user": None, **how}
        if not config.auth_enabled:
            return {"state": "open", "user": None, **how}
        user = await _session_user(request)
        if user is None:
            return {"state": "anonymous", "user": None, **how}
        if not user.authorised:
            state = "unauthorised"
        elif user.must_change_password:
            # Ordered above `authorised` on purpose: this account has a role and
            # still cannot use it, and the UI has to send them to the password
            # form rather than into an app where every request 428s.
            state = "password_change"
        else:
            state = "authorised"
        return {"state": state, "user": user.to_json(), **how}

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

    @router.post("/users")
    async def create_user(
        body: dict[str, Any], access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Create an account that signs in with a username and a password.

        Available whether or not password sign-in is switched on: an admin
        preparing accounts before flipping the flag is a reasonable order to
        work in, and the account simply cannot be used until it is on.
        """
        require_admin(access)
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        role = body.get("role")
        if not username:
            raise HTTPException(status_code=422, detail="a username is required")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"a password must be at least {MIN_PASSWORD_LENGTH} characters",
            )
        if role is not None and role not in ROLES:
            raise HTTPException(status_code=422, detail=f"role must be null or one of {ROLES}")

        try:
            created = await identity.create_local_user(
                username=username,
                password=password,
                role=role,
                email=str(body.get("email") or "").strip() or None,
                name=str(body.get("name") or "").strip() or None,
                # The admin knows this password, so it is temporary by
                # construction. `False` is honoured but has to be asked for.
                must_change_password=bool(body.get("mustChangePassword", True)),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        projects = body.get("projects")
        if isinstance(projects, list) and projects and access.user is not None:
            await _apply_projects(created.id, projects, access.user.id)
            refreshed = await identity.get(created.id)
            return {"user": refreshed.to_json() if refreshed else created.to_json()}
        return {"user": created.to_json()}

    @router.post("/users/{user_id}/password")
    async def reset_password(
        user_id: str, body: dict[str, Any], access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Set someone else's password.

        Always temporary: the person it is handed to must replace it before
        they can do anything, because until then two people know it.
        """
        require_admin(access)
        target = await identity.get(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no such user")
        if target.username is None:
            raise HTTPException(
                status_code=409,
                detail="this account signs in with Google and has no password to reset",
            )
        password = str(body.get("password") or "")
        try:
            await identity.set_password(user_id, password, must_change=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log.info(
            "%s reset the password for %s",
            access.user.username or access.user.email if access.user else "the service token",
            target.username,
        )
        updated = await identity.get(user_id)
        return {"user": updated.to_json() if updated else None}

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
        actor = access.user.id if access.user else None
        if actor is None:
            # The service credential has no user row to attribute a grant to,
            # and granted_by references users(id).
            raise HTTPException(
                status_code=403, detail="grants must be made by a signed-in admin"
            )
        await _apply_projects(user_id, body.get("projects"), actor)
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
