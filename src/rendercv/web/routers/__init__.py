"""
API routers for RenderCV SaaS.
"""

from rendercv.web.routers.auth import router as auth_router
from rendercv.web.routers.users import router as users_router
from rendercv.web.routers.cvs import router as cvs_router
from rendercv.web.routers.render import router as render_router
from rendercv.web.routers.api_keys import router as api_keys_router
from rendercv.web.routers.health import router as health_router

__all__ = [
    "auth_router",
    "users_router",
    "cvs_router",
    "render_router",
    "api_keys_router",
    "health_router",
]
