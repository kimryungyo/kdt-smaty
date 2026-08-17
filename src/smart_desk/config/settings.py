"""환경변수와 .env 파일에서 애플리케이션 설정을 읽는다."""

from __future__ import annotations

from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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
    # 아래 제어값은 relay 분리 검증을 위한 보수적인 초기 후보다. 실제 책상에서
    # 수신 간격과 관성을 측정한 뒤 문서와 함께 다시 확정한다.
    continuous_hold_ms: int = Field(default=500, ge=50, le=500)
    manual_hold_ms: int = Field(default=500, ge=50, le=500)
    fine_hold_ms: int = Field(default=100, ge=50, le=500)
    pulse_refresh_interval_seconds: float = Field(
        default=0.1,
        gt=0,
        le=0.5,
        allow_inf_nan=False,
    )
    control_poll_interval_seconds: float = Field(
        default=0.05,
        gt=0,
        le=0.5,
        allow_inf_nan=False,
    )
    manual_watchdog_seconds: float = Field(
        default=0.6,
        gt=0,
        le=5,
        allow_inf_nan=False,
    )
    target_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=300,
        allow_inf_nan=False,
    )
    target_tolerance_cm: float = Field(
        default=0.2,
        gt=0,
        le=2,
        allow_inf_nan=False,
    )
    fine_approach_distance_cm: float = Field(
        default=1.5,
        gt=0,
        le=10,
        allow_inf_nan=False,
    )
    fine_settle_seconds: float = Field(
        default=1.0,
        gt=0,
        le=10,
        allow_inf_nan=False,
    )
    relay_stale_after_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60,
        allow_inf_nan=False,
    )
    relay_ack_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=10,
        allow_inf_nan=False,
    )
    wake_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )

    @field_validator(
        "continuous_hold_ms",
        "manual_hold_ms",
        "fine_hold_ms",
        mode="before",
    )
    @classmethod
    def reject_boolean_hold_ms(cls, value: object) -> object:
        """bool이 릴레이 시간 정수로 변환되는 것을 막는다."""

        if isinstance(value, bool):
            raise ValueError("릴레이 hold 시간은 bool일 수 없습니다.")
        return value

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
        refresh_ms = self.pulse_refresh_interval_seconds * 1000
        if refresh_ms >= self.continuous_hold_ms:
            raise ValueError("연속 pulse 갱신 주기는 continuous hold보다 짧아야 합니다.")
        if refresh_ms >= self.manual_hold_ms:
            raise ValueError("연속 pulse 갱신 주기는 manual hold보다 짧아야 합니다.")
        if self.control_poll_interval_seconds > self.pulse_refresh_interval_seconds:
            raise ValueError("제어 poll 주기는 pulse 갱신 주기보다 길 수 없습니다.")
        if self.manual_watchdog_seconds <= self.control_poll_interval_seconds:
            raise ValueError("수동 watchdog은 제어 poll 주기보다 길어야 합니다.")
        if self.fine_approach_distance_cm <= self.target_tolerance_cm:
            raise ValueError("미세 접근 거리는 목표 허용 오차보다 커야 합니다.")
        if self.relay_stale_after_seconds <= self.relay_ack_timeout_seconds:
            raise ValueError("릴레이 stale 기준은 ack timeout보다 길어야 합니다.")
        if self.wake_timeout_seconds <= self.control_poll_interval_seconds:
            raise ValueError("sensor wake timeout은 제어 poll 주기보다 길어야 합니다.")
        return self


class CameraMediaSettings(BaseModel):
    """카메라 한 대의 독립적인 송출과 RTSP 수신 설정을 보관한다."""

    publish_enabled: bool = False
    receive_enabled: bool = False
    device: str
    publish_url: str
    receive_url: str
    input_format: str = "mjpeg"
    width: int = Field(default=1280, gt=0, le=8192)
    height: int = Field(default=720, gt=0, le=8192)
    fps: int = Field(default=15, gt=0, le=240)

    @field_validator("device", "publish_url", "receive_url", "input_format")
    @classmethod
    def normalize_required_string(cls, value: str) -> str:
        """공백을 제거하고 카메라 media 설정의 빈 문자열을 거부한다."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("카메라 media 설정은 비어 있을 수 없습니다.")
        return normalized

    @field_validator("publish_url", "receive_url")
    @classmethod
    def require_rtsp_url(cls, value: str) -> str:
        """현재 CameraPublisher와 RtspFrameSource가 지원하는 RTSP만 허용한다."""

        parsed = urlsplit(value)
        if parsed.scheme != "rtsp" or not parsed.netloc:
            raise ValueError("카메라 media URL은 유효한 rtsp:// 주소여야 합니다.")
        return value

    @field_validator("width", "height", "fps", mode="before")
    @classmethod
    def reject_boolean_capture_values(cls, value: object) -> object:
        """bool이 capture 크기나 FPS 정수로 변환되는 것을 막는다."""

        if isinstance(value, bool):
            raise ValueError("카메라 capture 값은 bool일 수 없습니다.")
        return value


class UserCameraMediaSettings(CameraMediaSettings):
    """사용자 카메라의 기본 장치와 MediaMTX 경로를 정의한다."""

    device: str = (
        "/dev/v4l/by-id/usb-Alcorlink_Corp._USB_2.0_Camera-video-index0"
    )
    publish_url: str = "rtsp://127.0.0.1:8554/user-cam"
    receive_url: str = "rtsp://127.0.0.1:8554/user-cam"
    width: int = Field(default=1920, gt=0, le=8192)
    height: int = Field(default=1080, gt=0, le=8192)


class WorkspaceCameraMediaSettings(CameraMediaSettings):
    """책상 전체 카메라의 기본 장치와 MediaMTX 경로를 정의한다."""

    device: str = (
        "/dev/v4l/by-id/usb-SunplusIT_Inc_ABKO_APC930_QHD_WEBCAM_"
        "CY2M20201014V0-video-index0"
    )
    publish_url: str = "rtsp://127.0.0.1:8554/workspace-cam"
    receive_url: str = "rtsp://127.0.0.1:8554/workspace-cam"
    width: int = Field(default=2592, gt=0, le=8192)
    height: int = Field(default=1944, gt=0, le=8192)


class PostureCameraMediaSettings(CameraMediaSettings):
    """자세 카메라의 기본 장치와 MediaMTX 경로를 정의한다."""

    device: str = "/dev/posture-cam"
    publish_url: str = "rtsp://127.0.0.1:8554/posture-cam"
    receive_url: str = "rtsp://127.0.0.1:8554/posture-cam"


class MediaSettings(BaseModel):
    """카메라별 송출과 수신 lifecycle 설정을 보관한다."""

    ffmpeg_path: str = "ffmpeg"
    rtsp_reconnect_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )
    user: UserCameraMediaSettings = Field(default_factory=UserCameraMediaSettings)
    workspace: WorkspaceCameraMediaSettings = Field(
        default_factory=WorkspaceCameraMediaSettings
    )
    posture: PostureCameraMediaSettings = Field(
        default_factory=PostureCameraMediaSettings
    )

    @field_validator("ffmpeg_path")
    @classmethod
    def normalize_ffmpeg_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("FFmpeg 경로는 비어 있을 수 없습니다.")
        return normalized


class VisionSettings(BaseModel):
    """최신 frame 기반 Vision 관측의 보수적인 초기 실행값이다.

    ROI와 detector threshold는 실제 카메라 실측 전에는 확정하지 않는다. 이 설정은
    frame/result freshness와 polling cadence만 한곳에서 정의한다.
    """

    poll_interval_seconds: float = Field(default=0.1, gt=0, le=2, allow_inf_nan=False)
    frame_stale_after_seconds: float = Field(default=1.0, gt=0, le=30, allow_inf_nan=False)
    result_stale_after_seconds: float = Field(default=1.0, gt=0, le=30, allow_inf_nan=False)
    stable_after_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    max_camera_skew_seconds: float = Field(default=0.5, gt=0, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_freshness(self) -> VisionSettings:
        if self.result_stale_after_seconds < self.poll_interval_seconds:
            raise ValueError("Vision result 만료 시간은 poll 주기보다 짧을 수 없습니다.")
        return self


class AutomationSettings(BaseModel):
    """자동 목표의 실제 Desk 실행 여부만 제어한다.

    시간과 높이는 제품 안전 계약의 고정값이라 환경 설정으로 노출하지 않는다.
    """

    execute_automatic_movements: bool = False


class StorageSettings(BaseModel):
    """로컬 영속 데이터 저장 경로를 보관한다."""

    database_path: Path = Path("data/smart_desk.db")


class DashboardSettings(BaseModel):
    """Dashboard React 정적 빌드 설정을 보관한다."""

    serve_frontend: bool = True
    frontend_directory: Path = Path("frontend/dist")


class WledSettings(BaseModel):
    """선택적으로 연결하는 단일 WLED 장치 설정이다."""

    enabled: bool = False
    base_url: str = "http://wled.local"
    timeout_seconds: float = Field(default=2.0, gt=0, le=10, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("WLED URL은 http:// 또는 https:// 주소여야 합니다.")
        if parsed.query or parsed.fragment:
            raise ValueError("WLED URL에는 query 또는 fragment를 사용할 수 없습니다.")
        return normalized


class OpenAiSettings(BaseModel):
    """AI 음성 turn에 사용하는 OpenAI API 설정을 보관한다."""

    api_key: SecretStr | None = None
    response_model: str = "gpt-5.6-terra"
    reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    transcription_model: str = "gpt-transcribe"
    transcription_prompt: str | None = Field(default=None, max_length=200)
    speech_model: str = "gpt-4o-mini-tts"
    speech_voice: str = "marin"
    transcription_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=60,
        allow_inf_nan=False,
    )
    response_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        allow_inf_nan=False,
    )
    speech_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        allow_inf_nan=False,
    )

    @field_validator(
        "response_model",
        "transcription_model",
        "speech_model",
        "speech_voice",
    )
    @classmethod
    def normalize_required_string(cls, value: str) -> str:
        """모델과 음성 이름의 공백을 제거하고 빈 값을 거부한다."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("OpenAI 모델과 음성 이름은 비어 있을 수 없습니다.")
        return normalized

    @field_validator("transcription_prompt", mode="before")
    @classmethod
    def normalize_optional_prompt(cls, value: object) -> object:
        """빈 transcription prompt를 설정되지 않은 값으로 정규화한다."""

        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class VoiceSettings(BaseModel):
    """로컬 microphone, Wake Word와 speaker 동작 설정을 보관한다."""

    enabled: bool = False
    input_device_name: str | None = None
    output_device_name: str | None = None

    wakeword_model_path: Path = Path(
        "assets/voice/models/hi_smarty_ko_synthetic_v0_1_0.onnx"
    )
    wakeword_threshold: float = Field(default=0.35, gt=0, le=1, allow_inf_nan=False)
    wakeword_consecutive_frames: int = Field(default=1, ge=1, le=5)
    wakeword_inference_interval_frames: int = Field(default=5, ge=1, le=25)

    silence_rms_threshold: float = Field(
        default=500.0,
        gt=0,
        le=32_767,
        allow_inf_nan=False,
    )
    speech_start_consecutive_frames: int = Field(default=2, ge=1, le=5)
    silence_duration_seconds: float = Field(
        default=0.6,
        ge=0.24,
        le=3.0,
        allow_inf_nan=False,
    )
    speech_start_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=15,
        allow_inf_nan=False,
    )
    min_utterance_seconds: float = Field(
        default=0.24,
        ge=0.16,
        le=2.0,
        allow_inf_nan=False,
    )
    max_utterance_seconds: float = Field(
        default=10.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )

    followup_enabled: bool = True
    followup_timeout_seconds: float = Field(
        default=4.0,
        gt=0,
        le=30,
        allow_inf_nan=False,
    )
    followup_preroll_seconds: float = Field(
        default=0.3,
        ge=0.08,
        le=1.0,
        allow_inf_nan=False,
    )
    post_playback_guard_seconds: float = Field(
        default=0.25,
        ge=0,
        le=2.0,
        allow_inf_nan=False,
    )
    input_queue_frames: int = Field(default=64, ge=8, le=256)
    session_max_turns: int = Field(default=12, ge=1, le=50)

    acknowledgement_effect_path: Path = Path(
        "assets/voice/effects/acknowledgement.wav"
    )
    error_effect_path: Path = Path("assets/voice/effects/error.wav")

    @field_validator("input_device_name", "output_device_name", mode="before")
    @classmethod
    def normalize_optional_device_name(cls, value: object) -> object:
        """빈 장치 이름을 기본 장치 선택을 뜻하는 None으로 정규화한다."""

        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @model_validator(mode="after")
    def validate_voice_timings(self) -> VoiceSettings:
        """녹음과 follow-up 설정 사이의 불변 조건을 검증한다."""

        if self.min_utterance_seconds >= self.max_utterance_seconds:
            raise ValueError("최소 발화 시간은 최대 발화 시간보다 짧아야 합니다.")
        if self.silence_duration_seconds >= self.max_utterance_seconds:
            raise ValueError("무음 종료 시간은 최대 발화 시간보다 짧아야 합니다.")
        preroll_frames = ceil(self.followup_preroll_seconds / 0.08)
        if preroll_frames >= self.input_queue_frames:
            raise ValueError("pre-roll frame 수는 입력 queue 크기보다 작아야 합니다.")
        if (
            self.followup_enabled
            and self.post_playback_guard_seconds >= self.followup_timeout_seconds
        ):
            raise ValueError("재생 후 guard는 follow-up timeout보다 짧아야 합니다.")
        return self


class VoiceDebugSettings(BaseModel):
    """임시 AI 스피커 관측 페이지의 별도 HTTP 서버 설정이다."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=10_000, ge=1, le=65_535)

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Voice debug host는 비어 있을 수 없습니다.")
        return normalized


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
    media: MediaSettings = MediaSettings()
    vision: VisionSettings = VisionSettings()
    automation: AutomationSettings = AutomationSettings()
    storage: StorageSettings = StorageSettings()
    dashboard: DashboardSettings = DashboardSettings()
    wled: WledSettings = WledSettings()
    openai: OpenAiSettings = OpenAiSettings()
    voice: VoiceSettings = VoiceSettings()
    voice_debug: VoiceDebugSettings = VoiceDebugSettings()

    @model_validator(mode="after")
    def require_voice_api_key(self) -> Settings:
        """Voice가 활성화되면 OpenAI API key를 필수로 요구한다."""

        if self.voice.enabled and self.openai.api_key is None:
            raise ValueError("Voice가 활성화되면 OpenAI API key가 필요합니다.")
        if self.voice_debug.enabled and not self.voice.enabled:
            raise ValueError("Voice debug를 활성화하려면 Voice가 활성화되어야 합니다.")
        if self.voice_debug.enabled and self.voice_debug.port == self.server.port:
            raise ValueError("Voice debug 포트는 기본 서버 포트와 달라야 합니다.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스에서 한 번만 검증한 설정을 반환한다."""

    return Settings()


def reset_settings_cache() -> None:
    """테스트에서만 설정 singleton 캐시를 비운다."""

    get_settings.cache_clear()
