"""The four roles, and username/password sign-in.

The capability tests are the point of this file. A role that is too *narrow*
announces itself the moment somebody tries to work — a 403 in the UI. A role
that is too *broad* is silent: nothing fails, and an author quietly turns out to
be able to queue a hundred tasks against somebody else's Claude account. So
every test below asserts both halves, what the role may do and what it may not.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.identity import (
    CAP_DESIGN,
    CAP_MANAGE_PROJECTS,
    CAP_MANAGE_USERS,
    CAP_RUN_TASKS,
    CAP_VIEW,
    MIN_PASSWORD_LENGTH,
    ROLE_ADMIN,
    ROLE_AUTHOR,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    ROLES,
    SESSION_COOKIE,
    IdentityStore,
    capabilities_for,
    hash_password,
    normalise_username,
    verify_password,
)
from dex.projects import ProjectStore


@pytest.fixture
def passwords(config, db, monkeypatch):
    """An app with password sign-in on, plus the store behind it."""
    enabled = dataclasses.replace(
        config, password_auth=True, google_client_id="", google_client_secret="", token=""
    )
    monkeypatch.setattr(
        "dex.queue.TaskRunner",
        __import__("tests.conftest", fromlist=["InstantRunner"]).InstantRunner,
    )
    with TestClient(create_app(enabled)) as client:
        yield client, IdentityStore(db), enabled


@pytest.fixture
def roles(config, db, monkeypatch):
    """An app with sign-in on and a helper that mints a session for a role."""
    enabled = dataclasses.replace(
        config, password_auth=True, google_client_id="", google_client_secret="", token=""
    )
    monkeypatch.setattr(
        "dex.queue.TaskRunner",
        __import__("tests.conftest", fromlist=["InstantRunner"]).InstantRunner,
    )
    identity = IdentityStore(db)
    with TestClient(create_app(enabled)) as client:
        async def as_role(role, projects=(), handle=None):
            name = handle or f"{role or 'none'}-{len(projects)}"
            user = await identity.create_local_user(
                username=name, password="a-long-enough-password", role=role,
                must_change_password=False,
            )
            if projects:
                await identity.set_projects(user.id, list(projects), user.id)
            secret = await identity.create_session(user.id, "pytest")
            client.cookies.set(SESSION_COOKIE, secret)
            return await identity.get(user.id)

        yield client, as_role, identity


# ------------------------------------------------------------ the role table --


def test_no_role_is_the_absence_of_one_rather_than_a_role():
    """`None` must never become a value, or it starts carrying capabilities."""
    assert "none" not in ROLES
    assert None not in ROLES
    assert capabilities_for(None) == frozenset()
    assert capabilities_for("none") == frozenset()


def test_every_role_can_read_and_only_admin_can_do_everything():
    for role in ROLES:
        assert CAP_VIEW in capabilities_for(role), role
    assert capabilities_for(ROLE_ADMIN) == frozenset(
        {CAP_VIEW, CAP_RUN_TASKS, CAP_DESIGN, CAP_MANAGE_PROJECTS, CAP_MANAGE_USERS}
    )


def test_the_roles_are_not_a_hierarchy():
    """Author and operator are different jobs, not different amounts of one."""
    author = capabilities_for(ROLE_AUTHOR)
    operator = capabilities_for(ROLE_OPERATOR)
    assert CAP_DESIGN in author and CAP_DESIGN not in operator
    assert CAP_RUN_TASKS in operator and CAP_RUN_TASKS not in author
    # Neither administers anything, which is the whole point of separating them.
    for role in (ROLE_AUTHOR, ROLE_OPERATOR, ROLE_VIEWER):
        assert CAP_MANAGE_USERS not in capabilities_for(role), role
        assert CAP_MANAGE_PROJECTS not in capabilities_for(role), role


def test_a_viewer_can_only_read():
    assert capabilities_for(ROLE_VIEWER) == frozenset({CAP_VIEW})


# ------------------------------------------------------------ password hashes --


def test_a_password_verifies_against_its_own_hash_and_nothing_else():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("correct horse battery stapl", stored)
    assert not verify_password("", stored)


def test_two_hashes_of_one_password_differ():
    """Salted, so a dump does not reveal who shares a password."""
    assert hash_password("same") != hash_password("same")


def test_an_account_with_no_password_cannot_be_signed_into_with_one():
    """A Google-only row has `password_hash` NULL; that is not a free pass."""
    assert not verify_password("anything", None)
    assert not verify_password("anything", "")


def test_a_corrupt_hash_denies_rather_than_raises():
    for bad in ("nonsense", "pbkdf2_sha256$x$y", "argon2$1$a$b", "$$$"):
        assert not verify_password("x", bad), bad


def test_a_username_is_matched_case_insensitively():
    assert normalise_username("  Admin ") == "admin"


# -------------------------------------------------------------- signing in ----


async def test_the_first_admin_is_seeded_and_must_replace_its_password(passwords):
    client, identity, config = passwords
    seeded = await identity.authenticate(config.seed_admin_username, config.seed_admin_password)
    assert seeded is not None
    assert seeded.role == ROLE_ADMIN
    # The pair is a door, not a credential: it works once, for one thing.
    assert seeded.must_change_password


async def test_a_second_admin_is_never_seeded(passwords):
    """Running twice must not mint another default account."""
    client, identity, config = passwords
    before = await identity.count_admins()
    assert await identity.ensure_seed_admin(config.seed_admin_username, "other") is None
    assert await identity.count_admins() == before


async def test_seeding_refuses_to_promote_an_existing_account_of_that_name(db):
    """Otherwise whoever holds `admin`'s password inherits the installation."""
    identity = IdentityStore(db)
    await identity.create_local_user(
        username="admin", password="a-long-enough-password", role=ROLE_VIEWER,
        must_change_password=False,
    )
    assert await identity.ensure_seed_admin("admin", "admin") is None
    unchanged = await identity.authenticate("admin", "a-long-enough-password")
    assert unchanged is not None and unchanged.role == ROLE_VIEWER


async def test_signing_in_with_a_password_sets_a_session(passwords):
    client, identity, _ = passwords
    await identity.create_local_user(
        username="ada", password="a-long-enough-password", role=ROLE_OPERATOR,
        must_change_password=False,
    )
    response = client.post(
        "/api/auth/login", json={"username": "ada", "password": "a-long-enough-password"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == ROLE_OPERATOR
    assert client.cookies.get(SESSION_COOKIE)
    assert client.get("/api/auth/me").json()["state"] == "authorised"


async def test_a_wrong_password_and_an_unknown_user_are_indistinguishable(passwords):
    client, identity, _ = passwords
    await identity.create_local_user(
        username="ada", password="a-long-enough-password", role=ROLE_VIEWER,
        must_change_password=False,
    )
    wrong = client.post("/api/auth/login", json={"username": "ada", "password": "nope"})
    missing = client.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


async def test_a_username_signs_in_whatever_its_case(passwords):
    client, identity, _ = passwords
    await identity.create_local_user(
        username="Ada", password="a-long-enough-password", role=ROLE_VIEWER,
        must_change_password=False,
    )
    assert client.post(
        "/api/auth/login", json={"username": "ADA", "password": "a-long-enough-password"}
    ).status_code == 200


async def test_login_is_absent_when_password_sign_in_is_off(config, db, monkeypatch):
    monkeypatch.setattr(
        "dex.queue.TaskRunner",
        __import__("tests.conftest", fromlist=["InstantRunner"]).InstantRunner,
    )
    off = dataclasses.replace(config, password_auth=False, token="")
    with TestClient(create_app(off)) as client:
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).status_code == 404
        assert client.get("/api/auth/config").json()["passwordEnabled"] is False


# ------------------------------------------------- the forced password change --


async def test_a_temporary_password_unlocks_nothing_but_its_own_replacement(passwords):
    """The whole point of the flag: the account works for exactly one thing."""
    client, identity, config = passwords
    signed = client.post(
        "/api/auth/login",
        json={"username": config.seed_admin_username, "password": config.seed_admin_password},
    )
    assert signed.status_code == 200
    assert signed.json()["user"]["mustChangePassword"] is True

    # 428, not 403: the UI has to tell these apart, because one is "ask an
    # admin" and the other is "you have one form to fill in".
    assert client.get("/api/projects").status_code == 428
    assert client.get("/api/auth/users").status_code == 428
    assert client.get("/api/auth/me").json()["state"] == "password_change"

    changed = client.post(
        "/api/auth/password",
        json={"current": config.seed_admin_password, "new": "a-real-admin-password"},
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["mustChangePassword"] is False
    # And now the account works, in the same tab, without signing in again.
    assert client.get("/api/auth/users").status_code == 200


async def test_a_short_password_is_refused(passwords):
    client, identity, config = passwords
    client.post(
        "/api/auth/login",
        json={"username": config.seed_admin_username, "password": config.seed_admin_password},
    )
    response = client.post(
        "/api/auth/password",
        json={"current": config.seed_admin_password, "new": "x" * (MIN_PASSWORD_LENGTH - 1)},
    )
    assert response.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in response.json()["detail"]


async def test_changing_a_password_needs_the_current_one(roles):
    client, as_role, identity = roles
    await as_role(ROLE_VIEWER, handle="vera")
    refused = client.post(
        "/api/auth/password", json={"current": "not-my-password", "new": "a-brand-new-password"}
    )
    assert refused.status_code == 403
    # And the old password still works, i.e. the refusal was not partial.
    assert await identity.authenticate("vera", "a-long-enough-password") is not None


async def test_a_password_reset_ends_every_session_the_user_had(roles):
    """A reset answers a leak. Leaving old sessions alive makes it cosmetic."""
    client, as_role, identity = roles
    victim = await as_role(ROLE_OPERATOR, handle="victim")
    stolen = client.cookies.get(SESSION_COOKIE)
    assert client.get("/api/auth/me").json()["state"] == "authorised"

    await identity.set_password(victim.id, "a-fresh-password", must_change=True)

    client.cookies.set(SESSION_COOKIE, stolen)
    assert client.get("/api/auth/me").json()["state"] == "anonymous"


# ------------------------------------------------- what each role may do ------


async def test_an_author_designs_and_does_not_queue_work(roles, db, config):
    client, as_role, _ = roles
    await ProjectStore(db, config.assets_dir).create("Alpha", slug="alpha")
    await as_role(ROLE_AUTHOR, projects=["alpha"])

    # Reading, and rewriting the project's standing guide: their job.
    assert client.get("/api/projects").status_code == 200
    assert client.put(
        "/api/projects/alpha/guide", json={"text": "# Alpha\n\nBuild things.\n"}
    ).status_code == 200

    # Queueing a task is not.
    queued = client.post(
        "/api/tasks", json={"problem": "please build something", "project": "alpha"}
    )
    assert queued.status_code == 403
    assert "run_tasks" in queued.json()["detail"]


async def test_an_operator_queues_work_and_does_not_redesign_the_project(roles, db, config):
    client, as_role, _ = roles
    await ProjectStore(db, config.assets_dir).create("Alpha", slug="alpha")
    await as_role(ROLE_OPERATOR, projects=["alpha"])

    assert client.post(
        "/api/tasks", json={"problem": "please build something", "project": "alpha"}
    ).status_code in (200, 201)

    refused = client.put("/api/projects/alpha/guide", json={"text": "mine now"})
    assert refused.status_code == 403
    assert "design" in refused.json()["detail"]


async def test_a_viewer_can_read_and_change_nothing(roles, db, config):
    client, as_role, _ = roles
    await ProjectStore(db, config.assets_dir).create("Alpha", slug="alpha")
    await as_role(ROLE_VIEWER, projects=["alpha"])

    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/packages?project=alpha").status_code == 200

    for method, url, body in (
        ("post", "/api/tasks", {"problem": "build something", "project": "alpha"}),
        ("put", "/api/projects/alpha/guide", {"text": "no"}),
        ("post", "/api/projects", {"name": "Mine"}),
        ("put", "/api/settings", {"paused": True}),
        ("post", "/api/chat/plan", {"message": "do a thing", "project": "alpha"}),
    ):
        response = getattr(client, method)(url, json=body)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"


async def test_a_role_does_not_reach_into_a_project_it_was_not_granted(roles, db, config):
    """The capability says what; the grant says where. Both have to allow it."""
    client, as_role, _ = roles
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")
    await as_role(ROLE_AUTHOR, projects=["alpha"])

    assert client.put("/api/projects/alpha/guide", json={"text": "# Alpha\n"}).status_code == 200
    # 404, not 403: a caller who cannot see the project is not told it exists.
    assert client.put("/api/projects/beta/guide", json={"text": "# Beta\n"}).status_code == 404


async def test_only_an_admin_administers(roles):
    client, as_role, _ = roles
    for role in (ROLE_AUTHOR, ROLE_OPERATOR, ROLE_VIEWER):
        await as_role(role, handle=f"admin-probe-{role}")
        assert client.get("/api/auth/users").status_code == 403, role
        assert client.post(
            "/api/auth/users", json={"username": "x", "password": "a-long-enough-password"}
        ).status_code == 403, role
        assert client.get("/api/costs").status_code == 403, role


async def test_an_admin_creates_an_account_with_a_role_and_projects(roles, db, config):
    client, as_role, identity = roles
    await ProjectStore(db, config.assets_dir).create("Alpha", slug="alpha")
    await as_role(ROLE_ADMIN, handle="boss")

    created = client.post("/api/auth/users", json={
        "username": "newbie",
        "password": "a-long-enough-password",
        "role": ROLE_OPERATOR,
        "projects": ["alpha"],
    })
    assert created.status_code == 200
    body = created.json()["user"]
    assert body["username"] == "newbie"
    assert body["role"] == ROLE_OPERATOR
    assert body["projects"] == ["alpha"]
    # Two people know this password, so it is temporary by construction.
    assert body["mustChangePassword"] is True


async def test_creating_a_user_reports_a_taken_username_rather_than_a_500(roles):
    client, as_role, _ = roles
    await as_role(ROLE_ADMIN, handle="boss2")
    first = {"username": "twice", "password": "a-long-enough-password", "role": ROLE_VIEWER}
    assert client.post("/api/auth/users", json=first).status_code == 200
    clash = client.post("/api/auth/users", json=first)
    assert clash.status_code == 409
    assert "username" in clash.json()["detail"]


async def test_an_unknown_role_is_refused_rather_than_stored(roles):
    client, as_role, _ = roles
    await as_role(ROLE_ADMIN, handle="boss3")
    for bad in ("none", "superuser", "user", ""):
        response = client.post("/api/auth/users", json={
            "username": f"bad-{bad or 'empty'}",
            "password": "a-long-enough-password",
            "role": bad,
        })
        assert response.status_code == 422, bad


async def test_a_google_account_has_no_password_to_reset(roles, db):
    client, as_role, identity = roles
    await as_role(ROLE_ADMIN, handle="boss4")
    google = await identity.upsert_google_user(
        {"sub": "g1", "email": "g@e.com", "email_verified": True}, set()
    )
    response = client.post(
        f"/api/auth/users/{google.id}/password", json={"password": "a-long-enough-password"}
    )
    assert response.status_code == 409
    assert "Google" in response.json()["detail"]


async def test_a_reset_password_must_be_replaced_by_whoever_receives_it(roles, db):
    client, as_role, identity = roles
    await as_role(ROLE_ADMIN, handle="boss5")
    target = await identity.create_local_user(
        username="forgetful", password="the-old-password", role=ROLE_OPERATOR,
        must_change_password=False,
    )
    assert client.post(
        f"/api/auth/users/{target.id}/password", json={"password": "a-temporary-password"}
    ).status_code == 200

    refreshed = await identity.get(target.id)
    assert refreshed.must_change_password
    assert await identity.authenticate("forgetful", "the-old-password") is None
    assert await identity.authenticate("forgetful", "a-temporary-password") is not None


async def test_the_last_admin_cannot_be_demoted_into_a_locked_installation(roles, db):
    client, as_role, identity = roles
    only = await as_role(ROLE_ADMIN, handle="solo")
    # This app seeded its own `admin` at startup, so there are two. Demoting
    # one is allowed precisely because the other is still there.
    seeded = await identity.authenticate("admin", "admin")
    assert await identity.count_admins() == 2
    await identity.delete_user(seeded.id)

    response = client.put(f"/api/auth/users/{only.id}/role", json={"role": ROLE_VIEWER})
    assert response.status_code == 409
    # And the refusal left them an admin rather than half-applying.
    assert (await identity.get(only.id)).role == ROLE_ADMIN


async def test_the_capabilities_reach_the_browser(roles, db, config):
    """The UI hides what a role cannot do, so it has to be told what that is."""
    client, as_role, _ = roles
    await ProjectStore(db, config.assets_dir).create("Alpha", slug="alpha")
    await as_role(ROLE_AUTHOR, projects=["alpha"])
    me = client.get("/api/auth/me").json()
    assert me["state"] == "authorised"
    assert sorted(me["user"]["capabilities"]) == sorted([CAP_DESIGN, CAP_VIEW])
