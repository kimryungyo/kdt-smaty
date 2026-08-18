"""SMART DESK HTTP 라우터를 조립한다."""

from fastapi import APIRouter

from smart_desk.api.routes.activity_modes import (
    activity_modes_router,
    profiles_router as activity_mode_profiles_router,
)
from smart_desk.api.routes.dashboard import router as dashboard_router
from smart_desk.api.routes.mode_usage import router as mode_usage_router
from smart_desk.api.routes.health import router as health_router
from smart_desk.api.routes.profiles import router as profiles_router
from smart_desk.api.routes.wled import router as wled_router
from smart_desk.api.routes.vision import router as vision_router
from smart_desk.api.routes.identity import router as identity_router
from smart_desk.api.routes.automation import router as automation_router, desk_router
from smart_desk.api.routes.assistant import router as assistant_router
from smart_desk.api.routes.voice import router as voice_router
from smart_desk.api.routes.tilt import router as tilt_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(dashboard_router)
api_router.include_router(profiles_router)
api_router.include_router(activity_mode_profiles_router)
api_router.include_router(activity_modes_router)
api_router.include_router(mode_usage_router)
api_router.include_router(wled_router)
api_router.include_router(vision_router)
api_router.include_router(identity_router)
api_router.include_router(automation_router)
api_router.include_router(desk_router)
api_router.include_router(assistant_router)
api_router.include_router(voice_router)
api_router.include_router(tilt_router)
