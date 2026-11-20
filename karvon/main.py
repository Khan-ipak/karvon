"""Application factory. Run with `uvicorn karvon.main:app`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from karvon import __version__
from karvon.api import router as api_router
from karvon.config import Settings
from karvon.db import make_engine, make_sessionmaker
from karvon.migrate import upgrade
from karvon.seed import seed
from karvon.services import DomainError, NotFound
from karvon.web import router as web_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    session_factory = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        upgrade(engine)
        with session_factory() as session, session.begin():
            seed(session, demo=settings.seed_demo)
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
        status = 404 if isinstance(exc, NotFound) else 409
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=status)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api_router)
    app.include_router(web_router)
    return app


app = create_app()
