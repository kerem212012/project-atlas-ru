from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from collections.abc import Callable

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import SessionDep, create_db_and_tables, get_session
from .models import Project, User
from .services import AIProvider, DemoProvider, GeminiProvider, LocalStorage, S3Storage

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
password_hash = PasswordHash.recommended()


def csrf_token(request: Request) -> str:
    token = request.session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        request.session["_csrf_token"] = token
    return token


def verify_csrf_token(request: Request, submitted_token: str) -> None:
    expected_token = request.session.get("_csrf_token")
    if not isinstance(expected_token, str) or not secrets.compare_digest(
        submitted_token, expected_token
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def current_user(request: Request, session: SessionDep) -> User | None:
    user_id = request.session.get("user_id")
    return session.get(User, user_id) if user_id is not None else None


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_storage() -> LocalStorage | S3Storage:
    if settings.storage_backend.lower() == "s3":
        return S3Storage()
    return LocalStorage(settings.local_storage_path)


def get_ai_provider() -> Callable[[], AIProvider]:
    if settings.ai_backend.lower() == "gemini":
        return GeminiProvider
    return DemoProvider


templates.env.globals["csrf_token"] = csrf_token


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="ProjectAtlas", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
if settings.storage_backend.lower() == "local":
    app.mount(
        "/uploads",
        StaticFiles(directory=settings.local_storage_path, check_dir=False),
        name="uploads",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ai": settings.ai_backend, "storage": settings.storage_backend}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep) -> HTMLResponse:
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    user = current_user(request, session)
    return templates.TemplateResponse(
        request, "index.html", {"projects": projects, "user": user}
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "register", "error": None}
    )


@app.post("/register", response_class=HTMLResponse, response_model=None)
def register(
    request: Request,
    session: SessionDep,
    username: str = Form(min_length=3, max_length=32),
    password: str = Form(min_length=8, max_length=128),
    submitted_csrf_token: str = Form(alias="csrf_token"),
) -> HTMLResponse | RedirectResponse:
    verify_csrf_token(request, submitted_csrf_token)
    username = username.strip()
    if not 3 <= len(username) <= 32:
        raise HTTPException(status_code=422, detail="Invalid username length")
    if session.exec(select(User).where(User.username == username)).first() is not None:
        return templates.TemplateResponse(
            request,
            "auth.html",
            {"mode": "register", "error": "Username is already taken."},
            status_code=409,
        )
    user = User(username=username, password_hash=password_hash.hash(password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return templates.TemplateResponse(
            request,
            "auth.html",
            {"mode": "register", "error": "Username is already taken."},
            status_code=409,
        )
    session.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "login", "error": None}
    )


@app.post("/login", response_class=HTMLResponse, response_model=None)
def login(
    request: Request,
    session: SessionDep,
    username: str = Form(min_length=3, max_length=32),
    password: str = Form(min_length=8, max_length=128),
    submitted_csrf_token: str = Form(alias="csrf_token", default=""),
) -> HTMLResponse | RedirectResponse:
    verify_csrf_token(request, submitted_csrf_token)
    user = session.exec(select(User).where(User.username == username.strip())).first()
    if user is None or not password_hash.verify(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "auth.html",
            {"mode": "login", "error": "Incorrect username or password."},
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(
    request: Request,
    submitted_csrf_token: str = Form(alias="csrf_token", default=""),
) -> RedirectResponse:
    verify_csrf_token(request, submitted_csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/projects/new", response_class=HTMLResponse, response_model=None)
def new_project(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    user = current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "project_form.html", {"user": user, "error": None}
    )


@app.post("/projects", response_model=None)
async def create_project(
    request: Request,
    session: SessionDep,
    storage: LocalStorage | S3Storage = Depends(get_storage),
    ai_provider_factory: Callable[[], AIProvider] = Depends(get_ai_provider),
    title: str = Form(min_length=2, max_length=100),
    description: str = Form(min_length=10, max_length=5000),
    cover: UploadFile | None = File(default=None),
    improve_with_ai: bool = Form(default=False),
    submitted_csrf_token: str = Form(alias="csrf_token", default=""),
) -> RedirectResponse:
    verify_csrf_token(request, submitted_csrf_token)
    user = current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    title = title.strip()
    description = description.strip()
    if not 2 <= len(title) <= 100 or not 10 <= len(description) <= 5000:
        raise HTTPException(status_code=422, detail="Invalid project title or description")

    cover_key = None
    cover_url = None
    if cover is not None and cover.filename:
        content = await cover.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Cover image exceeds 5 MB")
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            extension, content_type = ".png", "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            extension, content_type = ".jpg", "image/jpeg"
        elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            extension, content_type = ".webp", "image/webp"
        else:
            raise HTTPException(status_code=415, detail="Unsupported cover image")
        if cover.content_type != content_type:
            raise HTTPException(status_code=415, detail="Unsupported cover image")
        cover_key, cover_url = await run_in_threadpool(
            storage.save, content, extension, content_type
        )

    if improve_with_ai:
        try:
            description = await ai_provider_factory().improve_description(title, description)
        except TimeoutError:
            if cover_key is not None:
                storage.delete(cover_key)
            raise HTTPException(status_code=504, detail="Description improvement timed out")
        except Exception:
            if cover_key is not None:
                storage.delete(cover_key)
            raise HTTPException(status_code=502, detail="Could not improve the description")

    project = Project(
        title=title,
        description=description,
        cover_url=cover_url,
        cover_key=cover_key,
        owner_id=user.id,
    )
    session.add(project)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if cover_key is not None:
            storage.delete(cover_key)
        raise
    session.refresh(project)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int, request: Request, session: SessionDep
) -> HTMLResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    user = current_user(request, session)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "project": project,
            "user": user,
            "is_owner": user is not None and user.id == project.owner_id,
            "csrf_token_value": csrf_token(request),
        },
    )


@app.post("/projects/{project_id}/delete")
def delete_project(
    project_id: int,
    request: Request,
    session: SessionDep,
    storage: LocalStorage | S3Storage = Depends(get_storage),
    submitted_csrf_token: str = Form(alias="csrf_token", default=""),
) -> RedirectResponse:
    verify_csrf_token(request, submitted_csrf_token)
    user = current_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    project = session.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    cover_key = project.cover_key
    if cover_key is not None:
        storage.delete(cover_key)
    session.delete(project)
    session.commit()
    return RedirectResponse("/", status_code=303)
