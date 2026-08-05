"""SMART DESK HTTP 라우터를 조립한다."""

from fastapi import APIRouter

from smart_desk.api.routes.health import router as health_router


api_router = APIRouter()
api_router.include_router(health_router)

