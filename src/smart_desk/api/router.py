"""SMART DESK HTTP 라우터를 조립한다."""

from fastapi import APIRouter

from smart_desk.api.routes.activity_modes import (
    activity_modes_router,
    profiles_router as activity_mode_profiles_router,
)
from smart_desk.api.routes.dashboard import router as dashboard_router
from smart_desk.api.routes.health import router as health_router
from smart_desk.api.routes.profiles import router as profiles_router
from smart_desk.api.routes.wled import router as wled_router
from smart_desk.api.routes.vision import router as vision_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(dashboard_router)
api_router.include_router(profiles_router)
api_router.include_router(activity_mode_profiles_router)
api_router.include_router(activity_modes_router)
api_router.include_router(wled_router)
api_router.include_router(vision_router)
