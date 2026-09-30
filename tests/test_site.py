import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google import genai
from sqlmodel import Session, SQLModel, create_engine

from app import main, services


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
    main.app.dependency_overrides[main.get_ai_provider] = lambda: services.DemoProvider
    monkeypatch.setattr(main, "create_db_and_tables", lambda: None)
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
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


def test_publish_ai_cover_and_owner_boundary(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **_: pytest.fail("Local tests must not create a Gemini client"),
    )
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
    assert "Learning map" in detail.text
    assert "A clear weekly learning map" in detail.text
    assert "Proposed measurable outcome" in detail.text
    saved = list((tmp_path / "uploads").iterdir())
    assert len(saved) == 1 and saved[0].name == Path(saved[0].name).name
    assert re.fullmatch(r"[0-9a-f-]{36}\.png", saved[0].name)
    project_id = int(created.headers["location"].rstrip("/").split("/")[-1])
    logged_out = client.post(
        "/logout", data={"csrf_token": token(detail)}, follow_redirects=False
    )
    assert logged_out.status_code == 303
    register(client, "stranger")
    foreign_delete = client.post(
        f"/projects/{project_id}/delete",
        data={"csrf_token": token(client.get("/"))},
        follow_redirects=False,
    )
    assert foreign_delete.status_code == 404

    client.post("/logout", data={"csrf_token": token(client.get("/"))})
    login_csrf = token(client.get("/login"))
    logged_in = client.post(
        "/login",
        data={
            "username": "owner",
            "password": "strong-pass-123",
            "csrf_token": login_csrf,
        },
        follow_redirects=False,
    )
    assert logged_in.status_code == 303
    owner_detail = client.get(created.headers["location"])
    deleted = client.post(
        f"/projects/{project_id}/delete",
        data={"csrf_token": token(owner_detail)},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert list((tmp_path / "uploads").iterdir()) == []


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
    mismatched_mime = client.post(
        "/projects",
        data={"title": "Wrong MIME", "description": "Long enough description", "csrf_token": token(client.get("/projects/new"))},
        files={"cover": ("cover.png", b"\x89PNG\r\n\x1a\nimage", "text/plain")},
    )
    assert mismatched_mime.status_code == 415


def test_project_creation_without_csrf_is_rejected(client):
    register(client, "no-csrf")
    response = client.post(
        "/projects",
        data={
            "title": "No CSRF project",
            "description": "A project request without a CSRF token",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "extension"),
    [
        ("cover.jpeg", b"\xff\xd8\xffimage", "image/jpeg", ".jpg"),
        ("cover.png", b"\x89PNG\r\n\x1a\nimage", "image/png", ".png"),
        ("cover.webp", b"RIFF\x00\x00\x00\x00WEBPimage", "image/webp", ".webp"),
    ],
)
def test_supported_cover_formats_are_saved_with_detected_extension(
    client, tmp_path, filename, content, content_type, extension
):
    register(client, f"format-{extension[1:]}")
    response = client.post(
        "/projects",
        data={
            "title": "Image project",
            "description": "A project with a supported cover image",
            "csrf_token": token(client.get("/projects/new")),
        },
        files={"cover": (filename, content, content_type)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = list((tmp_path / "uploads").iterdir())
    assert len(saved) == 1 and saved[0].suffix == extension
    assert re.fullmatch(
        rf"[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-"
        rf"[0-9a-f]{{12}}{re.escape(extension)}",
        saved[0].name,
    )


def test_cover_over_5_mib_is_rejected(client):
    register(client, "large-cover")
    response = client.post(
        "/projects",
        data={
            "title": "Large cover",
            "description": "A project with an oversized cover image",
            "csrf_token": token(client.get("/projects/new")),
        },
        files={
            "cover": (
                "large.png",
                b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024),
                "image/png",
            )
        },
    )
    assert response.status_code == 413


def test_project_can_be_published_without_cover(client, tmp_path):
    register(client, "no-cover")
    response = client.post(
        "/projects",
        data={
            "title": "No cover project",
            "description": "A project that does not require a cover image",
            "csrf_token": token(client.get("/projects/new")),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not (tmp_path / "uploads").exists()


def test_ai_provider_is_called_only_when_checkbox_is_checked(client):
    class RecordingProvider:
        def __init__(self):
            self.calls = []

        async def improve_description(self, title: str, description: str) -> str:
            self.calls.append((title, description))
            return f"Improved {title}: {description}"

    provider = RecordingProvider()
    main.app.dependency_overrides[main.get_ai_provider] = lambda: lambda: provider
    register(client, "ai-checkbox")

    unchanged = client.post(
        "/projects",
        data={
            "title": "No AI project",
            "description": "This description should remain unchanged",
            "csrf_token": token(client.get("/projects/new")),
        },
        follow_redirects=False,
    )
    assert unchanged.status_code == 303
    assert provider.calls == []
    assert "This description should remain unchanged" in client.get(
        unchanged.headers["location"]
    ).text

    improved = client.post(
        "/projects",
        data={
            "title": "AI project",
            "description": "This description should be improved",
            "improve_with_ai": "true",
            "csrf_token": token(client.get("/projects/new")),
        },
        follow_redirects=False,
    )
    assert improved.status_code == 303
    assert provider.calls == [("AI project", "This description should be improved")]
    assert "Improved AI project" in client.get(improved.headers["location"]).text


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_detail"),
    [
        (TimeoutError("private timeout detail"), 504, "Description improvement timed out"),
        (RuntimeError("private provider detail"), 502, "Could not improve the description"),
    ],
)
def test_ai_provider_errors_are_mapped_safely(
    client, provider_error, expected_status, expected_detail
):
    class FailingProvider:
        async def improve_description(self, title: str, description: str) -> str:
            raise provider_error

    main.app.dependency_overrides[main.get_ai_provider] = lambda: lambda: FailingProvider()
    register(client, f"provider-error-{expected_status}")
    response = client.post(
        "/projects",
        data={
            "title": "AI failure",
            "description": "A project description for error handling",
            "improve_with_ai": "true",
            "csrf_token": token(client.get("/projects/new")),
        },
        follow_redirects=False,
    )
    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
