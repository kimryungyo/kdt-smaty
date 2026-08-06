"""환경변수와 .env 파일에서 애플리케이션 설정을 읽는다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from smart_desk.config.constants import (
    APP_NAME,
    DESK_CONTROL_MAX_CM,
    DESK_CONTROL_MIN_CM,
    DESK_PHYSICAL_MAX_CM,
    DESK_PHYSICAL_MIN_CM,
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


class SerialSettings(BaseModel):
    """Arduino 높이 리더의 시리얼 연결 설정을 보관한다."""

    port: str = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
    baudrate: int = Field(default=115200, ge=1)
    read_timeout_seconds: float = Field(
        default=0.2,
        gt=0,
        le=5,
        allow_inf_nan=False,
    )
    reconnect_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )

    @field_validator("port")
    @classmethod
    def normalize_port(cls, value: str) -> str:
        """장치 경로의 불필요한 공백을 제거하고 빈 값을 거부한다."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("시리얼 포트는 비어 있을 수 없습니다.")
        return normalized

    @field_validator("baudrate", mode="before")
    @classmethod
    def reject_boolean_baudrate(cls, value: object) -> object:
        """bool이 정수 baudrate로 변환되는 것을 막는다."""

        if isinstance(value, bool):
            raise ValueError("시리얼 baudrate는 bool일 수 없습니다.")
        return value


class DeskSettings(BaseModel):
    """센서 측정 범위와 그 안에서 허용하는 제어 범위를 보관한다."""

    measurement_min_cm: float = Field(
        default=DESK_PHYSICAL_MIN_CM,
        allow_inf_nan=False,
    )
    measurement_max_cm: float = Field(
        default=DESK_PHYSICAL_MAX_CM,
        allow_inf_nan=False,
    )
    operation_min_cm: float = Field(
        default=DESK_CONTROL_MIN_CM,
        allow_inf_nan=False,
    )
    operation_max_cm: float = Field(
        default=DESK_CONTROL_MAX_CM,
        allow_inf_nan=False,
    )
    height_stale_after_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )

    @property
    def physical_min_cm(self) -> float:
        """환경변수로 낮출 수 없는 실제 책상의 최소 높이를 반환한다."""

        return DESK_PHYSICAL_MIN_CM

    @property
    def physical_max_cm(self) -> float:
        """환경변수로 높일 수 없는 실제 책상의 최대 높이를 반환한다."""

        return DESK_PHYSICAL_MAX_CM

    @model_validator(mode="after")
    def validate_ranges(self) -> DeskSettings:
        """설정 범위가 고정된 물리·제어 경계를 넓히지 않는지 검증한다."""

        if self.measurement_min_cm >= self.measurement_max_cm:
            raise ValueError("책상 측정 최소 높이는 최대 높이보다 작아야 합니다.")
        if self.measurement_min_cm < DESK_PHYSICAL_MIN_CM:
            raise ValueError(
                f"책상 측정 최소 높이는 물리 하한 {DESK_PHYSICAL_MIN_CM:.0f}cm보다 "
                "낮을 수 없습니다."
            )
        if self.measurement_max_cm > DESK_PHYSICAL_MAX_CM:
            raise ValueError(
                f"책상 측정 최대 높이는 물리 상한 {DESK_PHYSICAL_MAX_CM:.0f}cm를 "
                "넘을 수 없습니다."
            )
        if self.operation_min_cm >= self.operation_max_cm:
            raise ValueError("책상 제어 최소 높이는 최대 높이보다 작아야 합니다.")
        if self.operation_min_cm < DESK_CONTROL_MIN_CM:
            raise ValueError(
                f"책상 제어 최소 높이는 제어 하한 {DESK_CONTROL_MIN_CM:.0f}cm보다 "
                "낮을 수 없습니다."
            )
        if self.operation_max_cm > DESK_CONTROL_MAX_CM:
            raise ValueError(
                f"책상 제어 최대 높이는 제어 상한 {DESK_CONTROL_MAX_CM:.0f}cm를 "
                "넘을 수 없습니다."
            )
        if self.operation_min_cm < self.measurement_min_cm:
            raise ValueError("책상 제어 최소 높이는 측정 범위 안이어야 합니다.")
        if self.operation_max_cm > self.measurement_max_cm:
            raise ValueError("책상 제어 최대 높이는 측정 범위 안이어야 합니다.")
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
    serial: SerialSettings = SerialSettings()
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
