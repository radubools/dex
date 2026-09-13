"""Google sign-in, roles, and the project scoping that hangs off them.

The scoping tests matter more than the OAuth ones. OAuth failing is visible
immediately; a scoping hole is silent, and dex's assets are addressed by path,
so one missed route lets a `user` read another project's work by typing its
name.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.identity import ROLE_ADMIN, ROLE_USER, SESSION_COOKIE, IdentityStore, decode_id_token, pkce_pair


def claims(sub: str, email: str, **extra):
    return {"sub": sub, "email": email, "email_verified": True, "name": email, **extra}


@pytest.fixture
def signed_in(config, db, monkeypatch):
    """An app with sign-in switched on, and a helper to mint sessions.

    `Config` is a frozen dataclass, so this replaces it rather than patching
    attributes onto it.
    """
    import dataclasses

    enabled = dataclasses.replace(
        config, google_client_id="cid", google_client_secret="secret", token=""
    )
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    identity = IdentityStore(db)

    with TestClient(create_app(enabled)) as client:
        async def sign_in(email, role, projects=()):
            user = await identity.upsert_google_user(claims(email, email), set())
            await identity.set_role(user.id, role)
            if projects:
                await identity.set_projects(user.id, list(projects), user.id)
            secret = await identity.create_session(user.id, "pytest")
            return user, secret

        yield client, sign_in, identity


# ----------------------------------------------------------------- identity --

def test_an_id_token_is_decoded_without_a_crypto_dependency():
    import base64, json
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": "1", "email": "a@b.c"}).encode()
    ).decode().rstrip("=")
    assert decode_id_token(f"header.{body}.sig")["email"] == "a@b.c"


def test_a_malformed_id_token_is_rejected_rather_than_trusted():
    for bad in ("", "nope", "a.b", "a.!!!.c"):
        with pytest.raises(ValueError):
            decode_id_token(bad)


def test_a_token_without_a_subject_is_rejected():
    import base64, json
    body = base64.urlsafe_b64encode(json.dumps({"email": "a@b.c"}).encode()).decode().rstrip("=")
    with pytest.raises(ValueError):
        decode_id_token(f"h.{body}.s")


def test_pkce_challenge_is_the_s256_of_its_verifier():
    import base64, hashlib
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert challenge == expected
    assert pkce_pair()[0] != verifier  # a fresh one each time


async def test_a_new_user_arrives_with_no_role(db):
    user = await IdentityStore(db).upsert_google_user(claims("s1", "nobody@example.com"), set())
    assert user.role is None
    assert user.authorised is False
    # And can see nothing at all, not even a project that exists.
    assert user.may_see("algorithms") is False


async def test_the_bootstrap_list_makes_the_first_admin(db):
    user = await IdentityStore(db).upsert_google_user(
        claims("s2", "Boss@Example.com"), {"boss@example.com"}
    )
    assert user.role == ROLE_ADMIN


async def test_bootstrap_promotes_but_never_restores_a_demotion(db):
    """Otherwise a stale env var quietly undoes the operator's decision."""
    identity = IdentityStore(db)
    user = await identity.upsert_google_user(claims("s3", "b@e.com"), {"b@e.com"})
    assert user.role == ROLE_ADMIN
    await identity.set_role(user.id, ROLE_USER)
    again = await identity.upsert_google_user(claims("s3", "b@e.com"), {"b@e.com"})
    assert again.role == ROLE_USER


async def test_a_session_resolves_to_the_live_role_not_a_snapshot(db):
    identity = IdentityStore(db)
    user = await identity.upsert_google_user(claims("s4", "u@e.com"), set())
    secret = await identity.create_session(user.id, "pytest")
    await identity.set_role(user.id, ROLE_USER)

    # The whole reason sessions are rows: the change is visible now.
    assert (await identity.user_for_session(secret)).role == ROLE_USER
    await identity.set_role(user.id, None)
    assert (await identity.user_for_session(secret)).authorised is False


async def test_the_cookie_secret_is_not_what_is_stored(db):
    """A dumped sessions table must not be replayable as somebody's cookie."""
    identity = IdentityStore(db)
    user = await identity.upsert_google_user(claims("s5", "u5@e.com"), set())
    secret = await identity.create_session(user.id, "pytest")
    stored = await db.pool.fetchval("SELECT id FROM sessions WHERE user_id = $1", user.id)
    assert stored != secret
    assert await identity.user_for_session(stored) is None


async def test_oauth_state_is_single_use(db):
    identity = IdentityStore(db)
    state = await identity.begin_oauth("verifier", "/somewhere")
    assert await identity.consume_oauth(state) == ("verifier", "/somewhere")
    # A replayed callback finds nothing.
    assert await identity.consume_oauth(state) is None


async def test_promoting_to_admin_clears_stale_grants(db):
    """An admin's access is the role; leftover grants would survive a demotion."""
    identity = IdentityStore(db)
    user = await identity.upsert_google_user(claims("s6", "u6@e.com"), set())
    await identity.set_role(user.id, ROLE_USER)
    await identity.set_projects(user.id, ["algorithms"], user.id)
    await identity.set_role(user.id, ROLE_ADMIN)
    assert (await identity.get(user.id)).projects == []


# ------------------------------------------------------------------ scoping --

def test_an_anonymous_caller_is_turned_away(signed_in):
    client, _, _ = signed_in
    assert client.get("/api/projects").status_code == 401


async def test_a_signed_in_user_with_no_role_is_told_so_distinctly(signed_in):
    """403, not 401: a 401 would send the UI into a sign-in loop forever."""
    client, sign_in, _ = signed_in
    _, secret = await sign_in("none@e.com", None)
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.get("/api/projects")
    assert response.status_code == 403
    assert "role" in response.json()["detail"]


async def test_a_user_sees_only_the_projects_granted_to_them(signed_in, config, db):
    client, sign_in, _ = signed_in
    # Two projects exist; the user is granted one.
    from dex.projects import ProjectStore
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")

    _, secret = await sign_in("scoped@e.com", ROLE_USER, projects=["alpha"])
    client.cookies.set(SESSION_COOKIE, secret)

    listed = client.get("/api/projects").json()
    assert [p["slug"] for p in listed["projects"]] == ["alpha"]
    # The default has to be something they can open, not the global default.
    assert listed["default"] == "alpha"

    # Threads in a project they were not granted are not found.
    assert client.get("/api/threads?project=beta").status_code == 404
    assert client.get("/api/threads?project=alpha").status_code == 200


async def test_assets_cannot_be_reached_by_typing_another_projects_path(signed_in, config, db):
    """The one surface where guessing a string would be enough."""
    from dex.projects import ProjectStore
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")
    (config.assets_dir / "beta" / "secret").mkdir(parents=True, exist_ok=True)
    (config.assets_dir / "beta" / "secret" / "x.md").write_text("private")

    client, sign_in, _ = signed_in
    _, secret = await sign_in("scoped2@e.com", ROLE_USER, projects=["alpha"])
    client.cookies.set(SESSION_COOKIE, secret)

    for url in (
        "/api/assets?path=beta/secret",
        "/api/assets/raw?path=beta/secret/x.md",
        "/api/assets/animation?path=beta/secret/x.md",
        "/api/assets/animation/play?path=beta/secret/x.md",
    ):
        assert client.get(url).status_code == 404, url
    # The assets root lists projects, so it is closed to a scoped user too.
    assert client.get("/api/assets?path=").status_code == 404


async def test_only_an_admin_may_create_a_project_or_touch_users(signed_in):
    client, sign_in, _ = signed_in
    _, secret = await sign_in("plain@e.com", ROLE_USER)
    client.cookies.set(SESSION_COOKIE, secret)
    assert client.post("/api/projects", json={"name": "Nope"}).status_code == 403
    assert client.get("/api/auth/users").status_code == 403


async def test_an_admin_sees_every_project_including_new_ones(signed_in, config, db):
    from dex.projects import ProjectStore
    client, sign_in, _ = signed_in
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    _, secret = await sign_in("boss@e.com", ROLE_ADMIN)
    client.cookies.set(SESSION_COOKIE, secret)
    # Created after the admin existed, and granted to nobody.
    await store.create("Gamma", slug="gamma")
    slugs = [p["slug"] for p in client.get("/api/projects").json()["projects"]]
    assert "gamma" in slugs and "alpha" in slugs


async def test_the_last_admin_cannot_be_demoted(signed_in):
    client, sign_in, identity = signed_in
    admin, secret = await sign_in("solo@e.com", ROLE_ADMIN)
    client.cookies.set(SESSION_COOKIE, secret)
    response = client.put(f"/api/auth/users/{admin.id}/role", json={"role": "user"})
    assert response.status_code == 409
    assert (await identity.get(admin.id)).is_admin


async def test_removing_a_role_ends_that_users_sessions(signed_in):
    """Otherwise they keep a cookie that now resolves to the unauthorised page."""
    client, sign_in, identity = signed_in
    admin, admin_secret = await sign_in("boss2@e.com", ROLE_ADMIN)
    victim, victim_secret = await sign_in("victim@e.com", ROLE_USER)

    client.cookies.set(SESSION_COOKIE, admin_secret)
    assert client.put(f"/api/auth/users/{victim.id}/role", json={"role": None}).status_code == 200
    assert await identity.user_for_session(victim_secret) is None


async def test_grants_are_replaced_not_merged(signed_in, config, db):
    """The admin UI sends checkbox state, so a missing slug means unticked."""
    from dex.projects import ProjectStore
    client, sign_in, identity = signed_in
    store = ProjectStore(db, config.assets_dir)
    for slug in ("alpha", "beta"):
        await store.create(slug.title(), slug=slug)
    admin, admin_secret = await sign_in("boss3@e.com", ROLE_ADMIN)
    target, _ = await sign_in("t@e.com", ROLE_USER, projects=["alpha", "beta"])

    client.cookies.set(SESSION_COOKIE, admin_secret)
    body = client.put(f"/api/auth/users/{target.id}/projects", json={"projects": ["beta"]})
    assert body.status_code == 200
    assert (await identity.get(target.id)).projects == ["beta"]


async def test_an_unknown_project_is_refused_rather_than_stored(signed_in):
    client, sign_in, _ = signed_in
    admin, admin_secret = await sign_in("boss4@e.com", ROLE_ADMIN)
    target, _ = await sign_in("t2@e.com", ROLE_USER)
    client.cookies.set(SESSION_COOKIE, admin_secret)
    response = client.put(
        f"/api/auth/users/{target.id}/projects", json={"projects": ["does-not-exist"]}
    )
    assert response.status_code == 422



async def test_per_id_routes_refuse_another_projects_task(signed_in, config, db):
    """The thirteen id-addressed routes, covered by a path-aware dependency.

    An id is as guessable as a slug once it has appeared in a log or a link, so
    "you need the id" is not an access control.
    """
    from dex.models import Task
    from dex.projects import ProjectStore
    from dex.store import TaskStore

    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")

    hidden = Task(problem="p", title="secret", slug="beta-secret")
    hidden.project = "beta"
    await TaskStore(db).create(hidden)

    client, sign_in, _ = signed_in
    _, secret = await sign_in("scoped3@e.com", ROLE_USER, projects=["alpha"])
    client.cookies.set(SESSION_COOKIE, secret)

    assert client.get(f"/api/tasks/{hidden.id}").status_code == 404
    assert client.get(f"/api/tasks/{hidden.id}/events").status_code == 404
    assert client.get(f"/api/tasks/{hidden.id}/messages").status_code == 404
    assert client.post(f"/api/tasks/{hidden.id}/cancel").status_code == 404
    assert client.post(f"/api/tasks/{hidden.id}/rerun").status_code == 404
    # And it is absent from the list, not merely unopenable.
    listed = client.get("/api/tasks").json()["tasks"]
    assert all(t["slug"] != "beta-secret" for t in listed)


async def test_a_user_cannot_enqueue_work_in_a_project_they_lack(signed_in, config, db):
    from dex.projects import ProjectStore
    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")

    client, sign_in, _ = signed_in
    _, secret = await sign_in("scoped4@e.com", ROLE_USER, projects=["alpha"])
    client.cookies.set(SESSION_COOKIE, secret)

    # `problem` has a min_length, so a too-short one is rejected as invalid
    # before the access check ever runs -- 422, not 404.
    assert client.post(
        "/api/tasks",
        json={"problem": "write something", "title": "x", "slug": "x", "project": "beta"},
    ).status_code == 404
    assert client.post(
        "/api/threads", json={"title": "t", "project": "beta"}
    ).status_code == 404
    assert client.post(
        "/api/chat/plan", json={"message": "hi", "project": "beta"}
    ).status_code == 404
    # Their own project is fine.
    assert client.post("/api/threads", json={"title": "t", "project": "alpha"}).status_code == 200


async def test_global_controls_are_admin_only(signed_in):
    """Concurrency and effort apply to everyone's work, not just the caller's."""
    client, sign_in, _ = signed_in
    _, secret = await sign_in("scoped5@e.com", ROLE_USER)
    client.cookies.set(SESSION_COOKIE, secret)

    assert client.put("/api/settings", json={"task_concurrency": 16}).status_code == 403
    assert client.get("/api/costs").status_code == 403
    # Readable, so the menu still renders -- but without other projects' spend.
    body = client.get("/api/settings")
    assert body.status_code == 200
    assert body.json()["costs"] == {}
    assert body.json()["readOnly"] is True


async def test_the_event_stream_carries_only_permitted_projects(signed_in, config, db):
    """A scoped subscriber must not receive another project's events by replay."""
    from dex.bus import EventBus
    from dex.models import Event
    from dex.projects import ProjectStore

    store = ProjectStore(db, config.assets_dir)
    await store.create("Alpha", slug="alpha")
    await store.create("Beta", slug="beta")

    bus = EventBus(db)
    await bus.start()
    try:
        bus.publish(Event(type="text", data={"text": "for alpha"}, project="alpha"))
        bus.publish(Event(type="text", data={"text": "for beta"}, project="beta"))
        bus.publish(Event(type="text", data={"text": "unscoped"}))
        await bus._pending.join()
    finally:
        await bus.stop()

    everything = await EventBus(db).history()
    assert {e.data.get("text") for e in everything} == {"for alpha", "for beta", "unscoped"}

    scoped = await EventBus(db).history(projects=["alpha"])
    texts = {e.data.get("text") for e in scoped}
    assert texts == {"for alpha"}
    # An untagged event reaches nobody who is restricted.
    assert "unscoped" not in texts
