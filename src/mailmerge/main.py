from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .api import router
from .auth import verify_clerk_token
from .config import settings
from .db import SessionLocal, init_db
from .profile_config import load_profiles


def find_frontend() -> Path | None:
    """Locate an explicit, packaged, or source-tree frontend build."""
    candidates = [
        settings.frontend_dir,
        settings.data_dir / "frontend",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
        Path.cwd() / "frontend" / "dist",
    ]
    for candidate in candidates:
        if candidate and (candidate / "index.html").is_file() and (candidate / "assets").is_dir():
            return candidate
    return None


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            auth_header = request.headers.get("Authorization", "")
            cookie_token = request.cookies.get("__session", "")

            tokens_to_try: list[str] = []
            if auth_header.startswith("Bearer "):
                tokens_to_try.append(auth_header.removeprefix("Bearer ").strip())
            if cookie_token:
                tokens_to_try.append(cookie_token.strip())

            is_valid = any(token and verify_clerk_token(token) for token in tokens_to_try)

            if not is_valid:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

            origin = request.headers.get("Origin")
            allowed_origins = {o.strip() for o in settings.frontend_origin.split(",") if o.strip()}
            if origin and origin not in allowed_origins:
                return JSONResponse({"detail": "origin rejected"}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.clerk.accounts.dev https://*.clerk.com; "
                "connect-src 'self' https://*.clerk.accounts.dev https://*.clerk.com https://api.clerk.com; "
                "img-src 'self' https://img.clerk.com data:; "
                "worker-src 'self' blob:; "
                "frame-src 'self' https://*.clerk.accounts.dev https://*.clerk.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:;"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cache-Control": "no-store",
        })
        return response


def create_app() -> FastAPI:
    init_db()
    if settings.profile_config_path.is_file():
        with SessionLocal() as db:
            load_profiles(settings.profile_config_path, db)
    app = FastAPI(title="Local Mail Merge", docs_url=None, redoc_url=None)
    app.add_middleware(LocalSecurityMiddleware)
    app.include_router(router)
    static = find_frontend()
    if static:
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}")
        def frontend(path: str):
            target = static / path
            return FileResponse(target if target.is_file() else static / "index.html")
    else:
        @app.get("/")
        def index():
            return {"app": "Local Mail Merge", "frontend": "not built"}
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("mailmerge.main:app", host=settings.host, port=settings.port)
