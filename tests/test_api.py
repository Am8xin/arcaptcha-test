"""REST API + persistence-across-restart tests."""

from app.db import Run, make_session_factory

from tests.conftest import wait_for_status

TERMINAL = ("SUCCESS", "FAILED", "BLOCKED", "TIMEOUT", "CANCELLED")


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_bots_endpoint_lists_registered_bots(client):
    """No clean_registry here: the registry holds the auto-discovered demo bot."""
    bots = client.get("/api/bots").json()
    names = {b["name"] for b in bots}
    assert "arcaptcha-demo" in names  # discovered from app/bots/

    demo = next(b for b in bots if b["name"] == "arcaptcha-demo")
    field_names = {f["name"] for f in demo["config_fields"]}
    assert {"username", "password"} <= field_names
    pw = next(f for f in demo["config_fields"] if f["name"] == "password")
    assert pw["secret"] is True
    assert pw["default"] == ""  # secrets are never sent to the client


def test_bots_endpoint_lists_scripted_bot(client, scripted_bot):
    bots = client.get("/api/bots").json()
    assert "scripted" in {b["name"] for b in bots}


def test_launch_via_api_and_follow_history(client, session_factory, scripted_bot):
    response = client.post(
        "/api/runs",
        json={"bot": "scripted", "mode": "headless", "config": {"username": "demo"}},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["id"] == run_id
    assert detail["bot"] == "scripted"
    assert detail["mode"] == "headless"

    # wait until finished, then check the persisted record
    run = wait_for_status(session_factory, run_id, TERMINAL)
    assert run.status == "SUCCESS"

    final = client.get(f"/api/runs/{run_id}").json()
    assert final["status"] == "SUCCESS"
    assert final["challenge_id"] == "ch-scripted-1"
    assert final["log"]


def test_launch_unknown_bot_404(client):
    assert client.post("/api/runs", json={"bot": "ghost", "mode": "headless"}).status_code == 404


def test_launch_repeat_creates_multiple_runs(client, session_factory, scripted_bot):
    """repeat=N launches N independent runs from a single API call."""
    response = client.post(
        "/api/runs",
        json={"bot": "scripted", "mode": "headless", "config": {}, "repeat": 3},
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["run_ids"]) == 3
    assert body["run_id"] == body["run_ids"][0]  # backward-compatible field

    for run_id in body["run_ids"]:
        run = wait_for_status(session_factory, run_id, TERMINAL)
        assert run.status == "SUCCESS"
    listed = {r["id"] for r in client.get("/api/runs", params={"bot": "scripted"}).json()}
    assert set(body["run_ids"]) <= listed


def test_launch_repeat_defaults_to_one(client, scripted_bot):
    body = client.post("/api/runs", json={"bot": "scripted", "mode": "headless"}).json()
    assert len(body["run_ids"]) == 1


def test_launch_repeat_out_of_range_422(client, scripted_bot):
    for bad in (0, 51):
        response = client.post(
            "/api/runs", json={"bot": "scripted", "mode": "headless", "repeat": bad}
        )
        assert response.status_code == 422


def test_launch_credentials_cycle_across_runs(client, session_factory, scripted_bot):
    """Run i uses credentials[i % len(credentials)]; passwords stay masked."""
    response = client.post(
        "/api/runs",
        json={
            "bot": "scripted",
            "mode": "headless",
            "repeat": 5,
            "credentials": [
                {"username": "alice", "password": "pw-a"},
                {"username": "bob", "password": "pw-b"},
            ],
        },
    )
    assert response.status_code == 202
    run_ids = response.json()["run_ids"]
    assert len(run_ids) == 5

    expected_usernames = ["alice", "bob", "alice", "bob", "alice"]
    for run_id, username in zip(run_ids, expected_usernames):
        wait_for_status(session_factory, run_id, TERMINAL)
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["config"]["username"] == username
        assert detail["config"]["password"] == "********"  # masked in history


def test_launch_credentials_empty_list_uses_defaults(client, scripted_bot):
    body = client.post(
        "/api/runs",
        json={"bot": "scripted", "mode": "headless", "credentials": []},
    ).json()
    detail = client.get(f"/api/runs/{body['run_id']}").json()
    assert detail["config"]["username"] == "demo"  # bot default still applies


def test_launch_credentials_blank_rows_are_dropped(client, scripted_bot):
    response = client.post(
        "/api/runs",
        json={
            "bot": "scripted",
            "mode": "headless",
            "credentials": [
                {"username": "", "password": ""},
                {"username": "alice", "password": "pw"},
            ],
        },
    )
    assert response.status_code == 202
    detail = client.get(f"/api/runs/{response.json()['run_id']}").json()
    assert detail["config"]["username"] == "alice"


def test_launch_credentials_partial_pair_fills_from_defaults(client, scripted_bot):
    """username-only pair: the password falls back to the bot default."""
    response = client.post(
        "/api/runs",
        json={
            "bot": "scripted",
            "mode": "headless",
            "credentials": [{"username": "alice", "password": ""}],
        },
    )
    assert response.status_code == 202
    detail = client.get(f"/api/runs/{response.json()['run_id']}").json()
    assert detail["config"]["username"] == "alice"
    assert detail["config"]["password"] == "********"  # default filled in + masked


def test_launch_credentials_more_than_50_422(client, scripted_bot):
    response = client.post(
        "/api/runs",
        json={
            "bot": "scripted",
            "mode": "headless",
            "credentials": [{"username": f"u{i}", "password": "p"} for i in range(51)],
        },
    )
    assert response.status_code == 422


def test_launch_invalid_mode_422(client, scripted_bot):
    response = client.post("/api/runs", json={"bot": "scripted", "mode": "stealth"})
    assert response.status_code == 422


def test_launch_missing_required_config_422(client, clean_registry):
    from app.bots.base import Bot, BotResult, ConfigField

    class Strict(Bot):
        name = "strict"
        title = "Strict"
        config_fields = (ConfigField("token", "Token", required=True),)

        def run(self, ctx):
            return BotResult(status="SUCCESS")

    clean_registry.register(Strict())
    response = client.post("/api/runs", json={"bot": "strict", "mode": "headless", "config": {}})
    assert response.status_code == 422


def test_run_not_found_404(client):
    assert client.get("/api/runs/nope").status_code == 404


def test_cancel_finished_run_conflict(client, session_factory, scripted_bot):
    run_id = client.post(
        "/api/runs", json={"bot": "scripted", "mode": "headless"}
    ).json()["run_id"]
    wait_for_status(session_factory, run_id, TERMINAL)
    response = client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 409


def test_stats_endpoint(client, session_factory, scripted_bot):
    run_id = client.post(
        "/api/runs", json={"bot": "scripted", "mode": "headless"}
    ).json()["run_id"]
    wait_for_status(session_factory, run_id, TERMINAL)
    stats = client.get("/api/stats").json()
    assert stats.get("SUCCESS", 0) >= 1


def test_runs_listing_filters(client, session_factory, scripted_bot):
    client.post("/api/runs", json={"bot": "scripted", "mode": "headless"})
    wait_for_status(session_factory,
                    client.get("/api/runs").json()[0]["id"], TERMINAL)
    rows = client.get("/api/runs", params={"bot": "scripted"}).json()
    assert rows and all(r["bot"] == "scripted" for r in rows)
    rows = client.get("/api/runs", params={"status": "SUCCESS"}).json()
    assert all(r["status"] == "SUCCESS" for r in rows)


def test_history_survives_engine_restart(engine, session_factory, runner, scripted_bot):
    """Data persistence: a fresh session over the SAME database keeps history."""
    run_id = runner.start_run("scripted", "headless", {"username": "u", "password": "p"})
    wait_for_status(session_factory, run_id, TERMINAL)

    fresh_factory = make_session_factory(engine)  # "restarted" process
    with fresh_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status == "SUCCESS"
        assert run.challenge_id == "ch-scripted-1"
