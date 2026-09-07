"""The door, and the shell that loads the app.

Everything a person sees is React now, with one exception: **the login stays here.** A
server-rendered form and an `httponly` cookie mean the secret never touches JavaScript
and cannot be read out of `localStorage` or devtools. The server owns the door, the
client owns the rooms.

Because the bundle is served same-origin by this app, that cookie is also all the auth
the API needs — no tokens, no CORS, no refresh dance.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

# Package data, found relative to __file__: an installed wheel has no repo root.
PACKAGE = Path(__file__).parent
TEMPLATE_DIR = PACKAGE / "templates"
STATIC_DIR = PACKAGE / "static"
# Vite's build output. Absent in a fresh checkout until `make ui`, so its absence has to
# be a readable message rather than a stack trace.
BUNDLE_DIR = STATIC_DIR / "app"
BUNDLE_INDEX = BUNDLE_DIR / "index.html"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
router = APIRouter()

COOKIE = "gogo_key"
SECRET_ENV = "GOGO_WEB_SECRET"
# One account until S5b brings real ones. Everything logged here belongs to it.
HANDLE_ENV = "GOGO_WEB_HANDLE"


def handle() -> str:
    return os.environ.get(HANDLE_ENV, "max")


def _secret() -> str | None:
    return os.environ.get(SECRET_ENV) or None


def signed_in(request: Request) -> bool:
    secret = _secret()
    return secret is not None and secrets.compare_digest(
        request.cookies.get(COOKIE, ""), secret
    )


def require_key(request: Request) -> None:
    """Gate for the JSON API.

    Off rather than open when unconfigured, and there is deliberately no "localhost is
    exempt" shortcut: behind a reverse proxy the client address is the proxy's, so that
    exemption would publish the whole thing.
    """
    if _secret() is None:
        raise HTTPException(status_code=503, detail=f"{SECRET_ENV} is not set")
    if not signed_in(request):
        raise HTTPException(status_code=401, detail="Not signed in")


def _off(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request, name="off.html", context={"env": SECRET_ENV}, status_code=503
    )


@router.get("/enter", response_class=HTMLResponse)
def enter(request: Request) -> Response:
    if _secret() is None:
        return _off(request)
    return templates.TemplateResponse(request=request, name="enter.html", context={})


@router.post("/enter")
def submit_key(request: Request, key: str = Form(...)) -> Response:
    secret = _secret()
    if secret is None:
        return _off(request)
    if not secrets.compare_digest(key.strip(), secret):
        return templates.TemplateResponse(
            request=request,
            name="enter.html",
            context={"error": "Not that one."},
            status_code=403,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE,
        secret,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    """Enough for "Add to Home Screen" to behave like an app."""
    return JSONResponse(
        {
            "name": "Gogo",
            "short_name": "Gogo",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b1622",
            "theme_color": "#0b1622",
            "icons": [],
        },
        media_type="application/manifest+json",
    )


# Registered last in api.py: this catches everything the routes above did not, so the
# client router can own /, /spot/:id and /log without the server knowing those paths.
@router.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa(request: Request, path: str) -> Response:
    if _secret() is None:
        return _off(request)
    if not signed_in(request):
        return RedirectResponse("/enter", status_code=303)
    if not BUNDLE_INDEX.is_file():
        return templates.TemplateResponse(
            request=request,
            name="unbuilt.html",
            context={"path": str(BUNDLE_DIR)},
            status_code=503,
        )
    # no-store: the shell is tiny and must never pin a stale asset manifest. The hashed
    # assets under /static/app/assets are the things worth caching.
    return FileResponse(BUNDLE_INDEX, headers={"Cache-Control": "no-store"})
