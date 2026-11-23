"""Application factory. Run with `uvicorn karvon.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from karvon import __version__, auth
from karvon.api import router as api_router
from karvon.config import Settings
from karvon.db import make_engine, make_sessionmaker
from karvon.migrate import upgrade
from karvon.models import Role, User
from karvon.seed import seed
from karvon.services import DomainError, NotFound
from karvon.web import router as web_router

log = logging.getLogger("karvon")


def bootstrap_admin(session, settings: Settings) -> None:
    if settings.admin_password:
        name = settings.admin_username.strip().lower()
        if session.scalar(select(User).where(User.username == name)) is None:
            auth.create_user(session, name, settings.admin_password, Role.admin, "Administrator")
            log.info("Created admin user %r from KARVON_ADMIN_PASSWORD", name)
    if auth.count_users(session) == 0:
        log.warning(
            "No users yet. Set KARVON_ADMIN_PASSWORD or run "
            "`python -m karvon.users create admin --role admin`"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    session_factory = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        upgrade(engine)
        with session_factory() as session, session.begin():
            seed(session, demo=settings.seed_demo)
            bootstrap_admin(session, settings)
        yield
        engine.dispose()

    app = FastAPI(
        title="Karvon",
        version=__version__,
        description="Open-source Terminal Operating System for land freight terminals.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.sessionmaker = session_factory

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = 409
        headers = None
        if isinstance(exc, NotFound):
            status = 404
        elif isinstance(exc, auth.NotAuthenticated):
            status = 401
            headers = {"WWW-Authenticate": 'Basic realm="karvon"'}
        elif isinstance(exc, auth.Forbidden):
            status = 403
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=status, headers=headers
        )

    @app.exception_handler(auth.LoginRequired)
    async def login_required(request: Request, exc: auth.LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api_router)
    app.include_router(web_router)
    return app


app = create_app()
