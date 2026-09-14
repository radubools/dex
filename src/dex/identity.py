"""Who is using dex, and what they are allowed to do.

Four roles, and no role at all:

- ``admin``    — runs the installation: creates projects and users, hands out
                 roles and project grants, resets passwords, and overrides
                 project settings. Sees every project, including ones made
                 after they were granted anything.
- ``author``   — shapes what a project *is*: its standing guide and its UI
                 widgets. Also runs tasks, because designing a project and
                 then being unable to try the thing you designed is not a job
                 anybody does — an author needs to see a task use the guide
                 they just wrote.
- ``operator`` — runs the work: queues tasks in the projects they are granted,
                 answers their questions, and writes project utilities.
- ``viewer``   — reads the library of generated material and nothing else.

A user with no role is signed in and not authorised. That is the absence of a
role rather than a role of its own, so it is spelled ``NULL`` and never appears
in ``ROLES``. It is deliberately a state rather than a rejection: an admin has
to see who has knocked before they can grant anything, and a silent 403 leaves
them nothing to act on.

So the four are a ladder, each adding to the one below: read, then run, then
design, then administer. Nothing in the code relies on that — every route asks
for the capability it needs and the table below answers — which is what lets a
role be widened, as ``author`` was, by editing one line rather than hunting for
the checks that assumed it could not run anything.

Project grants apply to all three non-admin roles: a role says what you may
do, `user_projects` says where. Both have to allow an action.

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

from asyncpg.exceptions import UniqueViolationError

from .db import Database

log = logging.getLogger("dex.identity")

#: How long a signed-in session lasts without re-authenticating.
SESSION_TTL_S = 30 * 24 * 3600

#: An OAuth round trip is seconds; anything older is a replay or a stale tab.
OAUTH_STATE_TTL_S = 600

SESSION_COOKIE = "dex_session"

ROLE_ADMIN = "admin"
ROLE_AUTHOR = "author"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

#: Every role a user may hold. No role is `None`, which is not in here.
ROLES = (ROLE_ADMIN, ROLE_AUTHOR, ROLE_OPERATOR, ROLE_VIEWER)

#: What a route asks for. Named for the action rather than the role, so a route
#: says what it needs and the table below decides who has it -- adding a role
#: later then touches this file and nothing else.
CAP_VIEW = "view"                      # read the library and everything in it
CAP_RUN_TASKS = "run_tasks"            # queue, answer, cancel, rerun
CAP_DESIGN = "design"                  # the project guide and its widgets
CAP_MANAGE_PROJECTS = "manage_projects"  # create and delete projects, settings
CAP_MANAGE_USERS = "manage_users"      # roles, grants, accounts, passwords

CAPABILITIES = (
    CAP_VIEW, CAP_RUN_TASKS, CAP_DESIGN, CAP_MANAGE_PROJECTS, CAP_MANAGE_USERS,
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    ROLE_ADMIN: frozenset(CAPABILITIES),
    ROLE_AUTHOR: frozenset({CAP_VIEW, CAP_RUN_TASKS, CAP_DESIGN}),
    ROLE_OPERATOR: frozenset({CAP_VIEW, CAP_RUN_TASKS}),
    ROLE_VIEWER: frozenset({CAP_VIEW}),
}

#: Shown in the admin UI, so the person assigning a role can see what it means
#: without reading this file.
ROLE_DESCRIPTIONS: dict[str, str] = {
    ROLE_ADMIN: "Creates projects and users, assigns roles and project access, "
                "resets passwords, overrides settings. Sees every project.",
    ROLE_AUTHOR: "Designs a project's guide and its UI widgets, and runs tasks, "
                 "in the projects they are given.",
    ROLE_OPERATOR: "Runs tasks and writes project utilities, in the projects "
                   "they are given.",
    ROLE_VIEWER: "Reads the library of generated material.",
}

#: What the old two-role model becomes. `user` could queue work in its granted
#: projects, which is `operator`; nothing it could do is lost.
LEGACY_ROLES = {"user": ROLE_OPERATOR}

#: PBKDF2-HMAC-SHA256, from the standard library. Not bcrypt or argon2: this is
#: a small number of accounts an admin creates by hand, and a hashing
#: dependency that has to be compiled is a worse trade here than iterations.
PBKDF2_ROUNDS = 480_000
_PBKDF2_PREFIX = "pbkdf2_sha256"

#: Short enough to be typed, long enough not to be guessed at leisure. The seed
#: admin's password is shorter than this on purpose and must be replaced.
MIN_PASSWORD_LENGTH = 8


def capabilities_for(role: str | None) -> frozenset[str]:
    """What `role` may do. No role means nothing at all."""
    return ROLE_CAPABILITIES.get(role or "", frozenset())


def hash_password(password: str) -> str:
    """A verifier for `password`, safe to store.

    Self-describing -- algorithm, rounds and salt travel with the hash -- so
    the round count can be raised later without a migration stranding the rows
    written under the old one.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "$".join((
        _PBKDF2_PREFIX,
        str(PBKDF2_ROUNDS),
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    ))


def verify_password(password: str, stored: str | None) -> bool:
    """Whether `password` matches `stored`.

    False for a user with no password rather than an error: a Google-only
    account has no `password_hash`, and "wrong password" is the honest answer to
    someone trying to sign into it with one.
    """
    if not stored:
        return False
    try:
        prefix, rounds, salt_b64, digest_b64 = stored.split("$")
        if prefix != _PBKDF2_PREFIX:
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
    except (ValueError, TypeError):
        log.warning("a stored password hash could not be parsed")
        return False
    # Constant time: a `==` on digests leaks how much of one matched.
    return secrets.compare_digest(expected, actual)


def normalise_username(raw: str) -> str:
    """The stored form of a username.

    Lowercased and trimmed, because a login typed with a capital is the same
    person and an install with both `Admin` and `admin` is a trap.
    """
    return raw.strip().lower()


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
    #: Project slugs this user may work in. Empty for an admin, whose access
    #: comes from the role rather than from grants — see `may_see`.
    projects: list[str] = field(default_factory=list)
    #: Set for an account that signs in with a password. None for a
    #: Google-only account, which is identified by its email instead.
    username: str | None = None
    #: True while a password an admin chose is still in place. Every route
    #: except "change my password" refuses until it is cleared, so handing
    #: someone a temporary password does not leave one working indefinitely.
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def capabilities(self) -> frozenset[str]:
        return capabilities_for(self.role)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def authorised(self) -> bool:
        """Whether this user has any access at all."""
        return self.role in ROLES

    def may_see(self, project: str | None) -> bool:
        """Whether this user may act within `project` at all.

        Separate from *what* they may do there: this is the `where`, and
        `can()` is the `what`. Both have to allow an action.

        An admin sees everything, including a project created after they were
        granted anything. Every other role sees exactly its grants, and `None`
        — an unscoped resource — is admin-only.
        """
        if self.is_admin:
            return True
        if not self.authorised:
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
            "username": self.username,
            "isAdmin": self.is_admin,
            "authorised": self.authorised,
            # Sent so the UI can hide what this person cannot do rather than
            # letting them press it and read a 403.
            "capabilities": sorted(self.capabilities),
            "mustChangePassword": self.must_change_password,
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

    @staticmethod
    def _user(row: Any, projects: list[str]) -> User:
        return User(
            id=row["id"], email=row["email"], name=row["name"], picture=row["picture"],
            role=row["role"], created_at=row["created_at"] or 0.0,
            last_seen=row["last_seen"], projects=projects,
            username=row["username"],
            must_change_password=bool(row["must_change_password"]),
        )

    async def get(self, user_id: str) -> User | None:
        row = await self.db.pool.fetchrow(
            """SELECT id, email, name, picture, role, username, must_change_password,
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
        return self._user(row, [g["project"] for g in grants])

    async def list_users(self) -> list[User]:
        rows = await self.db.pool.fetch(
            """SELECT u.id, u.email, u.name, u.picture, u.role, u.username,
                      u.must_change_password,
                      extract(epoch FROM u.created_at)::float8 AS created_at,
                      extract(epoch FROM u.last_seen)::float8  AS last_seen,
                      coalesce(
                        (SELECT array_agg(p.project ORDER BY p.project)
                           FROM user_projects p WHERE p.user_id = u.id), '{}'
                      ) AS projects
                 FROM users u
                -- Unassigned first: those are the ones waiting on the admin.
                ORDER BY (u.role IS NOT NULL), u.email"""
        )
        return [self._user(r, list(r["projects"] or [])) for r in rows]

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

    # ------------------------------------------------------ password accounts ----

    async def create_local_user(
        self,
        *,
        username: str,
        password: str,
        role: str | None,
        email: str | None = None,
        name: str | None = None,
        must_change_password: bool = True,
    ) -> User:
        """An account that signs in with a username and a password.

        `must_change_password` defaults true because an admin choosing somebody
        else's password means two people know it. The account works, and works
        for exactly one thing until they replace it.
        """
        handle = normalise_username(username)
        if not handle:
            raise ValueError("a username is required")
        if len(password) < MIN_PASSWORD_LENGTH and not must_change_password:
            raise ValueError(f"a password must be at least {MIN_PASSWORD_LENGTH} characters")
        if role is not None and role not in ROLES:
            raise ValueError(f"unknown role {role!r}")

        # `email` is NOT NULL and unique, and a local account need not have one.
        # A synthetic address keeps the column's promise without inventing a
        # real-looking mailbox that somebody might try to write to.
        address = (email or "").strip() or f"{handle}@local.dex"

        # Checked before the insert, not inferred from the violation
        # afterwards. A local account's synthetic address is derived from its
        # username, so a duplicate username collides with *both* unique
        # indexes and Postgres reports whichever it reached first -- which told
        # someone retyping a taken username that their email was the problem.
        if await self.db.pool.fetchval("SELECT 1 FROM users WHERE username = $1", handle):
            raise ValueError("that username is already in use")

        user_id = secrets.token_hex(8)
        try:
            await self.db.pool.execute(
                """INSERT INTO users (id, username, email, name, role,
                                      password_hash, must_change_password)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                user_id, handle, address, name or username.strip() or handle, role,
                hash_password(password), must_change_password,
            )
        except UniqueViolationError as exc:
            # Still handled: the check above is not atomic, and an email given
            # explicitly can collide on its own.
            field = "username" if exc.constraint_name == "users_username" else "email address"
            raise ValueError(f"that {field} is already in use") from exc
        log.info("created local user %s (role=%s)", handle, role or "none")
        created = await self.get(user_id)
        assert created is not None
        return created

    async def authenticate(self, username: str, password: str) -> User | None:
        """The user behind a correct username and password, or None.

        One answer for every kind of failure -- unknown user, no password set,
        wrong password -- so the response cannot be used to find out which
        usernames exist. The hash is still computed for a missing user, because
        returning early there makes the timing say it.
        """
        handle = normalise_username(username)
        row = await self.db.pool.fetchrow(
            "SELECT id, password_hash FROM users WHERE username = $1", handle
        )
        stored = row["password_hash"] if row else None
        if not verify_password(password, stored):
            if row is None:
                # Same work as a real verification, so a nonexistent user does
                # not answer measurably faster than a wrong password.
                verify_password(password, hash_password(_token()))
            log.info("failed password sign-in for %r", handle)
            return None
        await self.db.pool.execute(
            "UPDATE users SET last_seen = now() WHERE id = $1", row["id"]
        )
        return await self.get(row["id"])

    async def set_password(
        self, user_id: str, password: str, *, must_change: bool = False
    ) -> None:
        """Replace a user's password.

        Every other session of theirs ends. An admin resetting a password is
        usually responding to it having leaked, and leaving the old sessions
        alive would make the reset cosmetic.
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"a password must be at least {MIN_PASSWORD_LENGTH} characters")
        await self.db.pool.execute(
            """UPDATE users SET password_hash = $2, must_change_password = $3
                WHERE id = $1""",
            user_id, hash_password(password), must_change,
        )
        await self.end_all_sessions(user_id)

    async def ensure_seed_admin(self, username: str, password: str) -> User | None:
        """Create the first admin if the installation has none.

        Returns the account only when it made one, so the caller can say so in
        the log. Guarded on there being no admin rather than no users: a
        database full of people waiting for a role still needs a way in, and an
        install that already has an admin must never grow a second default one.
        """
        if await self.count_admins() > 0:
            return None
        handle = normalise_username(username)
        existing = await self.db.pool.fetchrow(
            "SELECT id FROM users WHERE username = $1", handle
        )
        if existing is not None:
            # The name is taken by a non-admin. Promoting it would hand whoever
            # holds that password the whole installation.
            log.warning(
                "not seeding an admin: the username %r already belongs to another account",
                handle,
            )
            return None
        return await self.create_local_user(
            username=handle, password=password, role=ROLE_ADMIN,
            name="Administrator", must_change_password=True,
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
