"""환경변수와 .env 파일에서 애플리케이션 설정을 읽는다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from smart_desk.config.constants import (
    APP_NAME,
    DESK_PHYSICAL_MAX_CM,
    SINGLE_PROCESS_WORKERS,
)


class ServerSettings(BaseModel):
    """FastAPI HTTP 서버 설정을 보관한다."""

    host: str = "0.0.0.0"
    port: int = Field(default=9090, ge=1, le=65535)
    workers: int = Field(default=SINGLE_PROCESS_WORKERS, ge=1)

    @model_validator(mode="after")
    def require_single_worker(self) -> ServerSettings:
        """하드웨어 singleton을 위해 worker 하나만 허용한다."""

        if self.workers != SINGLE_PROCESS_WORKERS:
            raise ValueError("SMART DESK는 Uvicorn worker를 하나만 사용할 수 있습니다.")
        return self


class MqttSettings(BaseModel):
    """MQTT broker 연결 설정을 보관한다."""

    host: str = "127.0.0.1"
    port: int = Field(default=1883, ge=1, le=65535)
    client_id: str = Field(default="smart-desk-server", min_length=1)
    keepalive_seconds: int = Field(default=30, ge=5, le=300)
    operation_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    reconnect_interval_seconds: float = Field(default=2.0, gt=0, le=30)


class DeskSettings(BaseModel):
    """높이 측정 범위와 물리 상한 안의 운영 범위를 보관한다."""

    measurement_min_cm: float = 73.0
    measurement_max_cm: float = 128.0
    operation_min_cm: float = 75.0
    operation_max_cm: float = DESK_PHYSICAL_MAX_CM

    @property
    def physical_max_cm(self) -> float:
        """환경변수로 높일 수 없는 실제 책상의 최대 높이를 반환한다."""

        return DESK_PHYSICAL_MAX_CM

    @model_validator(mode="after")
    def validate_ranges(self) -> DeskSettings:
        """운영 범위가 물리 측정 범위 안인지 검증한다."""

        if self.measurement_min_cm >= self.measurement_max_cm:
            raise ValueError("책상 측정 최소 높이는 최대 높이보다 작아야 합니다.")
        if self.operation_min_cm >= self.operation_max_cm:
            raise ValueError("책상 운영 최소 높이는 최대 높이보다 작아야 합니다.")
        if self.operation_min_cm < self.measurement_min_cm:
            raise ValueError("책상 운영 최소 높이는 측정 범위 안이어야 합니다.")
        if self.operation_max_cm > self.measurement_max_cm:
            raise ValueError("책상 운영 최대 높이는 측정 범위 안이어야 합니다.")
        if self.operation_max_cm > DESK_PHYSICAL_MAX_CM:
            raise ValueError(
                f"책상 운영 최대 높이는 물리 최대 높이 {DESK_PHYSICAL_MAX_CM:.0f}cm를 "
                "넘을 수 없습니다."
            )
        return self


class VisionSettings(BaseModel):
    """카메라 장치와 Vision 실행 설정을 보관한다."""

    user_camera: str = "/dev/user-cam"
    posture_camera: str = "/dev/video0"


class DashboardSettings(BaseModel):
    """Dashboard 데이터와 React 정적 빌드 설정을 보관한다."""

    data_directory: Path = Path("data")
    serve_frontend: bool = True
    frontend_directory: Path = Path("frontend/dist")


class Settings(BaseSettings):
    """시작 시 한 번 로드해 프로세스에서 공유하는 전체 설정 모델이다."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SMART_DESK_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_name: str = APP_NAME
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    server: ServerSettings = ServerSettings()
    mqtt: MqttSettings = MqttSettings()
    desk: DeskSettings = DeskSettings()
    vision: VisionSettings = VisionSettings()
    dashboard: DashboardSettings = DashboardSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스에서 한 번만 검증한 설정을 반환한다."""

    return Settings()


def reset_settings_cache() -> None:
    """테스트에서만 설정 singleton 캐시를 비운다."""

    get_settings.cache_clear()
