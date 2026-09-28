import re
from pathlib import Path

import app.main as main
import app.services as services
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine


def token(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "Render a hidden csrf_token in every mutating form"
    return match.group(1)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    required = ("get_session", "get_storage", "get_ai_provider")
    missing = [name for name in required if not hasattr(main, name)]
    missing += [
        f"services.{name}"
        for name in ("LocalStorage", "DemoProvider")
        if not hasattr(services, name)
    ]
    if missing:
        pytest.fail(f"Implement the final service boundaries first; missing: {', '.join(missing)}")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_session] = session_override
    main.app.dependency_overrides[main.get_storage] = lambda: services.LocalStorage(
        tmp_path / "uploads"
    )
    main.app.dependency_overrides[main.get_ai_provider] = services.DemoProvider
    monkeypatch.setattr(main, "create_db_and_tables", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
    engine.dispose()


def register(client, username):
    csrf = token(client.get("/register"))
    assert (
        client.post(
            "/register",
            data={"username": username, "password": "strong-pass-123", "csrf_token": csrf},
            follow_redirects=False,
        ).status_code
        == 303
    )


def test_health_home_guest_and_csrf(client):
    assert client.get("/health").status_code == 200
    assert "ProjectAtlas" in client.get("/").text
    assert client.get("/projects/new", follow_redirects=False).status_code == 303
    assert client.post(
        "/register", data={"username": "student", "password": "strong-pass-123"}
    ).status_code in {403, 422}


def test_publish_ai_cover_and_owner_boundary(client, tmp_path):
    register(client, "owner")
    csrf = token(client.get("/projects/new"))
    created = client.post(
        "/projects",
        data={
            "title": "Learning map",
            "description": "A clear weekly learning map",
            "improve_with_ai": "true",
            "csrf_token": csrf,
        },
        files={"cover": ("../cover.png", b"\x89PNG\r\n\x1a\ncourse-image", "image/png")},
        follow_redirects=False,
    )
    assert created.status_code == 303
    detail = client.get(created.headers["location"])
    assert "Learning map" in detail.text and "Outcome:" in detail.text
    saved = list((tmp_path / "uploads").iterdir())
    assert len(saved) == 1 and ".." not in saved[0].name
    project_id = int(created.headers["location"].rstrip("/").split("/")[-1])
    deleted = client.post(
        f"/projects/{project_id}/delete",
        data={"csrf_token": token(detail)},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert list((tmp_path / "uploads").iterdir()) == []
    project_id = int(created.headers["location"].rstrip("/").split("/")[-1])
    client.post("/logout", data={"csrf_token": token(detail)})
    register(client, "stranger")
    assert (
        client.post(
            f"/projects/{project_id}/delete", data={"csrf_token": token(client.get("/"))}
        ).status_code
        == 404
    )


def test_invalid_cover_is_rejected(client):
    register(client, "file-user")
    csrf = token(client.get("/projects/new"))
    response = client.post(
        "/projects",
        data={"title": "Bad file", "description": "Long enough description", "csrf_token": csrf},
        files={"cover": ("x.txt", b"x", "text/plain")},
    )
    assert response.status_code == 415
    csrf = token(client.get("/projects/new"))
    spoofed = client.post(
        "/projects",
        data={"title": "Fake image", "description": "Long enough description", "csrf_token": csrf},
        files={"cover": ("pretend.png", b"not-an-image", "image/png")},
    )
    assert spoofed.status_code == 415
