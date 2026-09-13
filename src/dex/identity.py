"""Who is using dex, and what they are allowed to see.

Three roles, and the third is the absence of one:

- ``admin``  — every project, plus the ability to hand out roles and grants.
- ``user``   — only the projects granted in ``user_projects``.
- ``None``   — signed in, not authorised. Deliberately a state rather than a
               rejection: the operator needs to see who has knocked before they
               can grant anything, and a silent 403 leaves them nothing to act
               on.

Sessions are rows, not self-contained tokens. A JWT would save a table and cost
the thing that matters here: revocation. Stripping a role has to take effect on
the next request, not whenever a signature happens to expire.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database

log = logging.getLogger("dex.identity")

#: How long a signed-in session lasts without re-authenticating.
SESSION_TTL_S = 30 * 24 * 3600

#: An OAuth round trip is seconds; anything older is a replay or a stale tab.
OAUTH_STATE_TTL_S = 600

SESSION_COOKIE = "dex_session"

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


def _token() -> str:
    """A value with enough entropy to be unguessable in a URL or a cookie."""
    return secrets.token_urlsafe(32)


def _hash(value: str) -> str:
    """What gets stored for a session.

    The cookie holds the secret; the table holds only this. A dumped database
    then cannot be replayed as somebody's session.
    """
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class User:
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    role: str | None = None
    created_at: float = 0.0
    last_seen: float | None = None
    #: Project slugs this user may see. Empty for an admin, whose access comes
    #: from the role rather than from grants — see `may_see`.
    projects: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def authorised(self) -> bool:
        """Whether this user has any access at all."""
        return self.role in ROLES

    def may_see(self, project: str | None) -> bool:
        """Whether this user may read or act within `project`.

        An admin sees everything, including a project created after they were
        granted anything. A `user` sees exactly their grants. Nobody else sees
        anything, and `None` — an unscoped resource — is admin-only.
        """
        if self.is_admin:
            return True
        if self.role != ROLE_USER:
            return False
        return project is not None and project in self.projects

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "role": self.role,
            "projects": self.projects,
            "isAdmin": self.is_admin,
            "authorised": self.authorised,
            "createdAt": self.created_at,
            "lastSeen": self.last_seen,
        }


def decode_id_token(id_token: str) -> dict[str, Any]:
    """The claims inside a Google ID token, without verifying the signature.

    Safe **only** because of where this token comes from: dex exchanges an
    authorization code with Google's token endpoint directly, over TLS, using
    its client secret. The token therefore arrives on a channel already
    authenticated as Google — it was never handed to us by the browser. Google's
    own guidance says signature validation is unnecessary in exactly this case,
    and skipping it keeps a JWKS fetch and a crypto dependency out of the login
    path.

    If this is ever changed to read a token from a redirect fragment or a
    client, the signature MUST be verified instead.
    """
    try:
        payload = id_token.split(".")[1]
        # JWT base64url omits padding; `b64decode` insists on it.
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("malformed id_token") from exc
    if not isinstance(claims, dict) or not claims.get("sub"):
        raise ValueError("id_token carries no subject")
    return claims


class IdentityStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------- oauth ----

    async def begin_oauth(self, code_verifier: str, redirect_to: str | None) -> str:
        """Record one login attempt and return its `state`.

        The verifier is kept server-side so the callback can prove it started
        the flow (PKCE), and `state` makes a forged callback useless.
        """
        state = _token()
        await self.db.pool.execute(
            """INSERT INTO oauth_states (state, code_verifier, redirect_to)
               VALUES ($1, $2, $3)""",
            state, code_verifier, redirect_to,
        )
        # Opportunistic cleanup; no scheduler needed for a table this small.
        await self.db.pool.execute(
            "DELETE FROM oauth_states WHERE created_at < now() - make_interval(secs => $1)",
            float(OAUTH_STATE_TTL_S),
        )
        return state

    async def consume_oauth(self, state: str) -> tuple[str, str | None] | None:
        """The verifier and destination for `state`, exactly once.

        Deleting as part of the read is what makes a callback single-use: a
        replayed code cannot mint a second session.
        """
        row = await self.db.pool.fetchrow(
            """DELETE FROM oauth_states
                WHERE state = $1
                  AND created_at > now() - make_interval(secs => $2)
             RETURNING code_verifier, redirect_to""",
            state, float(OAUTH_STATE_TTL_S),
        )
        return (row["code_verifier"], row["redirect_to"]) if row else None

    # -------------------------------------------------------------- users ----

    async def upsert_google_user(self, claims: dict[str, Any], admin_emails: set[str]) -> User:
        """Find or create the user behind a set of Google claims.

        Matched on `sub` first and email second: Google's subject is stable
        while an email can be renamed, but a user whose row predates their first
        login (seeded by email from DEX_ADMIN_EMAILS) has no `sub` yet.

        `admin_emails` is applied on every login, not just creation, so adding
        an address to the environment promotes that person on their next sign-in
        and does not silently do nothing.
        """
        sub = str(claims["sub"])
        email = str(claims.get("email") or "").strip()
        if not email:
            raise ValueError("Google returned no email for this account")
        name = claims.get("name")
        picture = claims.get("picture")
        bootstrap = email.lower() in {e.lower() for e in admin_emails}

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id FROM users
                        WHERE google_sub = $1 OR lower(email) = lower($2)
                        ORDER BY (google_sub = $1) DESC LIMIT 1""",
                    sub, email,
                )
                if row is None:
                    user_id = secrets.token_hex(8)
                    await conn.execute(
                        """INSERT INTO users (id, google_sub, email, name, picture, role, last_seen)
                           VALUES ($1, $2, $3, $4, $5, $6, now())""",
                        user_id, sub, email, name, picture,
                        ROLE_ADMIN if bootstrap else None,
                    )
                    log.info(
                        "new user %s (%s)%s", email, user_id,
                        " — admin by DEX_ADMIN_EMAILS" if bootstrap else " — no role yet",
                    )
                else:
                    user_id = row["id"]
                    await conn.execute(
                        """UPDATE users
                              SET google_sub = $2, email = $3, name = $4, picture = $5,
                                  last_seen = now(),
                                  -- Promotion only. An admin demoted in the UI
                                  -- must not be restored by a stale env var,
                                  -- but nor should a bootstrap address have to
                                  -- be granted by hand.
                                  role = CASE WHEN $6 AND role IS NULL THEN 'admin' ELSE role END
                            WHERE id = $1""",
                        user_id, sub, email, name, picture, bootstrap,
                    )
        found = await self.get(user_id)
        assert found is not None  # just written, in the same connection pool
        return found

    async def get(self, user_id: str) -> User | None:
        row = await self.db.pool.fetchrow(
            """SELECT id, email, name, picture, role,
                      extract(epoch FROM created_at)::float8 AS created_at,
                      extract(epoch FROM last_seen)::float8  AS last_seen
                 FROM users WHERE id = $1""",
            user_id,
        )
        if row is None:
            return None
        grants = await self.db.pool.fetch(
            "SELECT project FROM user_projects WHERE user_id = $1 ORDER BY project", user_id
        )
        return User(
            id=row["id"], email=row["email"], name=row["name"], picture=row["picture"],
            role=row["role"], created_at=row["created_at"] or 0.0,
            last_seen=row["last_seen"], projects=[g["project"] for g in grants],
        )

    async def list_users(self) -> list[User]:
        rows = await self.db.pool.fetch(
            """SELECT u.id, u.email, u.name, u.picture, u.role,
                      extract(epoch FROM u.created_at)::float8 AS created_at,
                      extract(epoch FROM u.last_seen)::float8  AS last_seen,
                      coalesce(
                        (SELECT array_agg(p.project ORDER BY p.project)
                           FROM user_projects p WHERE p.user_id = u.id), '{}'
                      ) AS projects
                 FROM users u
                -- Unassigned first: those are the ones waiting on the operator.
                ORDER BY (u.role IS NOT NULL), u.email"""
        )
        return [
            User(
                id=r["id"], email=r["email"], name=r["name"], picture=r["picture"],
                role=r["role"], created_at=r["created_at"] or 0.0,
                last_seen=r["last_seen"], projects=list(r["projects"] or []),
            )
            for r in rows
        ]

    async def set_role(self, user_id: str, role: str | None) -> None:
        if role is not None and role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        await self.db.pool.execute("UPDATE users SET role = $2 WHERE id = $1", user_id, role)
        if role == ROLE_ADMIN:
            # An admin's access is their role. Leaving grants behind would mean
            # a later demotion silently kept whatever they happened to have.
            await self.db.pool.execute("DELETE FROM user_projects WHERE user_id = $1", user_id)

    async def set_projects(self, user_id: str, projects: list[str], granted_by: str) -> None:
        """Replace this user's grants with exactly `projects`.

        A replace rather than an add, because the admin UI sends the state of a
        set of checkboxes: anything missing from it has been unticked.
        """
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM user_projects WHERE user_id = $1", user_id)
                if projects:
                    await conn.executemany(
                        """INSERT INTO user_projects (user_id, project, granted_by)
                           VALUES ($1, $2, $3)
                           ON CONFLICT DO NOTHING""",
                        [(user_id, p, granted_by) for p in projects],
                    )

    async def delete_user(self, user_id: str) -> None:
        """Remove a user entirely. Cascades to their grants and sessions."""
        await self.db.pool.execute("DELETE FROM users WHERE id = $1", user_id)

    async def count_admins(self) -> int:
        return int(
            await self.db.pool.fetchval("SELECT count(*) FROM users WHERE role = 'admin'") or 0
        )

    # ----------------------------------------------------------- sessions ----

    async def create_session(self, user_id: str, user_agent: str | None) -> str:
        """Start a session and return the secret to put in the cookie."""
        secret = _token()
        await self.db.pool.execute(
            """INSERT INTO sessions (id, user_id, expires_at, user_agent)
               VALUES ($1, $2, now() + make_interval(secs => $3), $4)""",
            _hash(secret), user_id, float(SESSION_TTL_S), (user_agent or "")[:300],
        )
        return secret

    async def user_for_session(self, secret: str | None) -> User | None:
        """The live user behind a cookie, or None.

        Reads the role and grants fresh every time. That is the point of
        server-side sessions: a role removed a second ago is gone now.
        """
        if not secret:
            return None
        row = await self.db.pool.fetchrow(
            "SELECT user_id FROM sessions WHERE id = $1 AND expires_at > now()", _hash(secret)
        )
        if row is None:
            return None
        return await self.get(row["user_id"])

    async def end_session(self, secret: str | None) -> None:
        if secret:
            await self.db.pool.execute("DELETE FROM sessions WHERE id = $1", _hash(secret))

    async def end_all_sessions(self, user_id: str) -> None:
        """Sign a user out everywhere — used when their access is taken away."""
        await self.db.pool.execute("DELETE FROM sessions WHERE user_id = $1", user_id)

    async def purge_expired(self) -> int:
        deleted = await self.db.pool.fetchval(
            "WITH d AS (DELETE FROM sessions WHERE expires_at <= now() RETURNING 1) "
            "SELECT count(*) FROM d"
        )
        return int(deleted or 0)


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    Guards the code exchange: an intercepted authorization code is useless
    without the verifier, which never leaves this server.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def now() -> float:
    return time.time()
