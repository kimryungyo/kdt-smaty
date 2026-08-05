"""Uvicorn이 불러오는 SMART DESK 애플리케이션 진입점."""

from smart_desk.application import create_application


app = create_application()

