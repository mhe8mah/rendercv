"""
Main FastAPI application for RenderCV SaaS.

This module creates and configures the FastAPI application with all
routes, middleware, and event handlers.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rendercv.web.config import get_settings
from rendercv.web.database import close_db, init_db
from rendercv.web.middleware import setup_middleware
from rendercv.web.routers import (
    api_keys_router,
    auth_router,
    cvs_router,
    health_router,
    render_router,
    users_router,
)

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting RenderCV SaaS API...")
    await init_db()
    logger.info("Database initialized")
    yield
    # Shutdown
    logger.info("Shutting down RenderCV SaaS API...")
    await close_db()
    logger.info("Database connections closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="""
# RenderCV SaaS API

A professional CV/Resume generation service API.

## Features

- **CV Management**: Create, edit, and manage your CVs
- **Multiple Themes**: Choose from classic, engineeringresumes, sb2nov, moderncv, and more
- **Multi-format Export**: Generate PDF, PNG, HTML, and Markdown
- **Real-time Validation**: Validate your CV YAML before rendering
- **Public Sharing**: Share your CVs with public links

## Authentication

This API uses JWT Bearer tokens for authentication. Include your token in the Authorization header:

```
Authorization: Bearer <your_token>
```

You can also use API keys for programmatic access:

```
Authorization: Bearer rcv_<your_api_key>
```

## Rate Limits

- Free tier: 10 renders/month
- Pro tier: 100 renders/month
- Enterprise tier: 1000 renders/month

API requests are limited to 60 per minute.
        """,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else "/api/openapi.json",
        lifespan=lifespan,
    )

    # Setup middleware
    setup_middleware(app)

    # Include routers
    api_prefix = "/api/v1"

    app.include_router(health_router, prefix=api_prefix)
    app.include_router(auth_router, prefix=api_prefix)
    app.include_router(users_router, prefix=api_prefix)
    app.include_router(cvs_router, prefix=api_prefix)
    app.include_router(render_router, prefix=api_prefix)
    app.include_router(api_keys_router, prefix=api_prefix)

    # Exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Handle Pydantic validation errors."""
        errors = []
        for error in exc.errors():
            errors.append(
                {
                    "field": ".".join(str(x) for x in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": "Validation Error",
                "detail": errors,
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger.exception("Unexpected error: %s", exc)

        if settings.debug:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "detail": str(exc),
                    "type": type(exc).__name__,
                },
            )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error"},
        )

    # Root endpoint
    @app.get("/")
    async def root():
        """Root endpoint with API information."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api": "/api/v1",
            "health": "/api/v1/health",
        }

    return app


# Create the application instance
app = create_app()


def run_server():
    """Run the development server."""
    import uvicorn

    uvicorn.run(
        "rendercv.web.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=1 if settings.debug else settings.workers,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    run_server()
