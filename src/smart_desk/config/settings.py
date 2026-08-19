"""환경변수와 .env 파일에서 애플리케이션 설정을 읽는다."""

from __future__ import annotations

from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator, model_validator
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


class TiltSettings(BaseModel):
    """틸팅 ESP32(모터드라이버)의 MQTT 단계 이동 모드 설정을 보관한다.

    실측 보정은 duty=100(UP/DOWN)만 있다. 펌웨어는 보정 없는 duty를 가장
    가까운 보정점(100)의 속도로 clamp하므로, move_duty_percent를 100이 아닌
    값으로 바꾸면 에러 없이 "성공"하지만 실제 이동 거리가 어긋난다. 추가
    보정 데이터를 확보하기 전에는 100을 유지한다.
    """

    enabled: bool = False
    # 장치와 어떻게 이야기할지. relay와 맞춰 mqtt를 기본으로 쓰고, 보드를
    # 직접 물려 확인할 때만 serial로 되돌린다.
    transport: Literal["mqtt", "serial"] = "mqtt"
    # 이 환경에는 ESP32-C3 native USB 장치가 relay-controller와 틸트 보드
    # 두 개 있어 glob 자동탐색은 쓰지 않는다. 실측된 by-id 경로로 고정한다.
    serial_port: str = (
        "/dev/serial/by-id/"
        "usb-Espressif_USB_JTAG_serial_debug_unit_B8:1F:3F:0C:F7:14-if00"
    )
    baudrate: int = Field(default=115200, ge=1)
    read_timeout_seconds: float = Field(default=0.2, gt=0, le=5, allow_inf_nan=False)
    write_timeout_seconds: float = Field(default=0.7, gt=0, le=5, allow_inf_nan=False)
    event_timeout_seconds: float = Field(default=1.0, gt=0, le=5, allow_inf_nan=False)
    reconnect_interval_seconds: float = Field(
        default=1.0, gt=0, le=30, allow_inf_nan=False
    )
    min_level: int = Field(default=0, ge=0)
    max_level: int = Field(default=4, ge=0)
    move_duty_percent: int = Field(default=100, ge=1, le=100)
    levels_file: Path = Path("data/tilt_levels.json")
    calibration_file: Path = Path("data/tilt_calibration.json")

    @field_validator("serial_port")
    @classmethod
    def normalize_serial_port(cls, value: str) -> str:
        """장치 경로의 불필요한 공백을 제거하고 빈 값을 거부한다."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("틸팅 시리얼 포트는 비어 있을 수 없습니다.")
        return normalized

    @field_validator("baudrate", "move_duty_percent", mode="before")
    @classmethod
    def reject_boolean_ints(cls, value: object) -> object:
        """bool이 정수 설정값으로 변환되는 것을 막는다."""

        if isinstance(value, bool):
            raise ValueError("틸팅 설정의 정수 값은 bool일 수 없습니다.")
        return value

    @model_validator(mode="after")
    def validate_levels(self) -> TiltSettings:
        """단계 범위가 뒤집히지 않았는지 검증한다."""

        if self.min_level >= self.max_level:
            raise ValueError("틸팅 최소 단계는 최대 단계보다 작아야 합니다.")
        return self


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
    # 실제 책상 relay의 접점 수명과 관성을 고려한 제어값이다. 목표 근처의
    # 짧은 ON/OFF 반복보다 한 번의 충분한 pulse와 재측정을 우선한다.
    continuous_hold_ms: int = Field(default=500, ge=50, le=500)
    manual_hold_ms: int = Field(default=500, ge=50, le=500)
    fine_hold_ms: int = Field(default=350, ge=50, le=500)
    max_fine_pulses: int = Field(default=2, ge=1, le=5)
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
        default=1.0,
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
        default=1.2,
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
        default=6.0,
        gt=0,
        le=10,
        allow_inf_nan=False,
    )
    wake_timeout_seconds: float = Field(
        default=8.0,
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
    """카메라 한 대의 독립적인 WHIP 송출과 WHEP 수신 설정을 보관한다."""

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
    def require_media_url(cls, value: str, info: ValidationInfo) -> str:
        """발행은 WHIP, 수신은 WHEP 또는 직접 MJPEG endpoint를 허용한다."""

        parsed = urlsplit(value)
        expected_suffixes = ("/whip",) if info.field_name == "publish_url" else ("/whep", "/stream")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or not parsed.path.rstrip("/").endswith(expected_suffixes)
        ):
            expected = ".../whip" if info.field_name == "publish_url" else ".../whep 또는 .../stream"
            raise ValueError(
                f"카메라 {info.field_name}은 유효한 http(s)://{expected} 주소여야 합니다."
            )
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
    publish_url: str = "http://127.0.0.1:8889/user-cam/whip"
    receive_url: str = "http://127.0.0.1:8889/user-cam/whep"
    width: int = Field(default=1920, gt=0, le=8192)
    height: int = Field(default=1080, gt=0, le=8192)


class WorkspaceCameraMediaSettings(CameraMediaSettings):
    """책상 전체 카메라의 기본 장치와 MediaMTX 경로를 정의한다."""

    device: str = (
        "/dev/v4l/by-id/usb-SunplusIT_Inc_ABKO_APC930_QHD_WEBCAM_"
        "CY2M20201014V0-video-index0"
    )
    publish_url: str = "http://127.0.0.1:8889/workspace-cam/whip"
    receive_url: str = "http://127.0.0.1:8889/workspace-cam/whep"
    width: int = Field(default=2592, gt=0, le=8192)
    height: int = Field(default=1944, gt=0, le=8192)


class PostureCameraMediaSettings(CameraMediaSettings):
    """자세 카메라의 기본 장치와 MediaMTX 경로를 정의한다."""

    device: str = "/dev/posture-cam"
    publish_url: str = "http://127.0.0.1:8889/posture-cam/whip"
    receive_url: str = "http://127.0.0.1:8889/bottom-cam/whep"


class MediaSettings(BaseModel):
    """카메라별 송출과 수신 lifecycle 설정을 보관한다."""

    reconnect_interval_seconds: float = Field(
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

class VisionSettings(BaseModel):
    """최신 frame 기반 Vision 관측의 보수적인 초기 실행값이다.

    detector threshold는 실제 카메라 실측 전에는 확정하지 않는다. 이 설정은
    frame/result freshness와 polling cadence만 한곳에서 정의한다.
    """

    poll_interval_seconds: float = Field(default=0.1, gt=0, le=2, allow_inf_nan=False)
    # 상·하단 YOLO 호출은 하나의 model lock에서 직렬화되어 운영 장비에서 결합 1회가
    # 약 0.8초 걸린다. 정상 처리 지연을 stale로 오판하지 않도록 3초 여유를 둔다.
    frame_stale_after_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    result_stale_after_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    stable_after_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    stability_majority_ratio: float = Field(
        default=0.7, gt=0.5, le=1.0, allow_inf_nan=False
    )
    stability_min_samples: int = Field(default=3, ge=2, le=100)
    # 하단 추론 간격은 장비 부하에 따라 변하므로 자세 안정화는 시간 대신 최근 distinct
    # sample 수를 쓴다. camera stale/연결 단절은 이 유예와 무관하게 즉시 차단한다.
    posture_transition_samples: int = Field(default=3, ge=2, le=30)
    posture_transition_required_samples: int = Field(default=2, ge=2, le=30)
    posture_unknown_samples: int = Field(default=6, ge=2, le=100)
    posture_recovery_samples: int = Field(default=2, ge=2, le=30)

    @model_validator(mode="after")
    def validate_posture_samples(self) -> VisionSettings:
        if self.posture_transition_required_samples > self.posture_transition_samples:
            raise ValueError("자세 전이 필요 sample 수는 전이 window보다 클 수 없습니다.")
        return self
    # 독립 WHEP receiver의 최신 frame 도착은 동일 15fps stream이라도 최대 약 0.5초
    # 어긋날 수 있다. result freshness(1초) 안에서만 결합하되 정상 scheduler jitter가
    # 안정화 timer를 계속 초기화하지 않도록 약간의 여유를 둔다.
    max_camera_skew_seconds: float = Field(default=0.75, gt=0, le=10, allow_inf_nan=False)
    # 하단 YOLO pose는 선택 기능이다. 경로가 비어 있으면 Noop detector로 안전하게 동작한다.
    lower_pose_model_path: Path | None = None
    # 같은 pose model을 상단 재실 인원 판정에도 별도 로드한다. 상단 얼굴 검출은
    # 프로필 식별 전용이며, 이 주기는 CPU 과점을 막기 위해 독립적으로 둔다.
    upper_inference_interval_seconds: float = Field(
        default=0.5, ge=0.5, le=10, allow_inf_nan=False
    )
    lower_inference_interval_seconds: float = Field(
        default=0.5, ge=0.5, le=10, allow_inf_nan=False
    )
    lower_pose_input_size: int = Field(default=640, ge=64, le=2048)
    # 상단 user-cam의 재실/다중 인원 판정은 오검출이 AUTO를 막지 않도록 하단 자세보다
    # 더 높은 사람 신뢰도를 요구한다.
    upper_presence_min_person_confidence: float = Field(default=0.60, ge=0, le=1)
    lower_pose_min_person_confidence: float = Field(default=0.30, ge=0, le=1)
    lower_pose_min_hip_confidence: float = Field(default=0.08, ge=0, le=1)
    lower_pose_min_knee_ankle_confidence: float = Field(default=0.45, ge=0, le=1)
    lower_pose_decision_threshold: float = Field(default=0.52, ge=0, le=1)

    @field_validator("lower_pose_model_path", mode="before")
    @classmethod
    def normalize_lower_pose_model_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_freshness(self) -> VisionSettings:
        if self.result_stale_after_seconds < self.poll_interval_seconds:
            raise ValueError("Vision result 만료 시간은 poll 주기보다 짧을 수 없습니다.")
        return self


class VisionClientSettings(BaseModel):
    """Main이 별도 Vision HTTP service를 사용할 때의 연결 설정이다."""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:9091"
    api_token: SecretStr | None = None
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=10, allow_inf_nan=False)
    request_timeout_seconds: float = Field(default=2.5, gt=0, le=30, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Vision service URL은 http:// 또는 https:// 주소여야 합니다.")
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Vision service URL에는 path, query, fragment를 사용할 수 없습니다.")
        return normalized


class VisionServerSettings(BaseModel):
    """Stateless Vision HTTP process가 읽는 API 설정이다."""

    host: str = "0.0.0.0"
    port: int = Field(default=9091, ge=1, le=65535)
    api_token: SecretStr | None = None


class FaceSettings(BaseModel):
    """Local OpenCV face models and conservative calibration candidates."""

    detector_model_path: Path | None = None
    embedding_model_path: Path | None = None
    detector_score_threshold: float = Field(default=0.85, ge=0, le=1, allow_inf_nan=False)
    detector_nms_threshold: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
    min_face_size: int = Field(default=64, ge=16, le=2048)
    min_blur_variance: float = Field(default=30.0, ge=0, le=100000, allow_inf_nan=False)
    min_brightness: float = Field(default=35.0, ge=0, le=255, allow_inf_nan=False)
    max_brightness: float = Field(default=220.0, ge=0, le=255, allow_inf_nan=False)
    match_threshold: float = Field(default=0.363, ge=-1, le=1, allow_inf_nan=False)
    best_second_margin: float = Field(default=0.04, ge=0, le=2, allow_inf_nan=False)
    pairwise_consistency_threshold: float = Field(default=0.363, ge=-1, le=1, allow_inf_nan=False)
    duplicate_threshold: float = Field(default=0.45, ge=-1, le=1, allow_inf_nan=False)
    enrollment_sample_interval_seconds: float = Field(default=0.5, gt=0, le=30, allow_inf_nan=False)

    @field_validator("detector_model_path", "embedding_model_path", mode="before")
    @classmethod
    def normalize_model_path(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_brightness(self) -> FaceSettings:
        if self.min_brightness >= self.max_brightness:
            raise ValueError("Face brightness minimum must be below maximum.")
        return self


class AutomationSettings(BaseModel):
    """자동 목표 실행과 완료 뒤 재보정 deadband를 제어한다."""

    execute_automatic_movements: bool = False
    # 앉고 서기를 이만큼 유지해야 책상이 따라 움직인다. 짧으면 잠깐 몸을 일으킬
    # 때마다 책상이 오르내려 오히려 방해가 된다.
    posture_confirmation_seconds: float = Field(default=5.0, gt=0, le=30, allow_inf_nan=False)
    # 후보와 다른 자세가 이 시간 안에 지나가면 흔들림으로 보고 세던 시간을
    # 지킨다. 자세 인식은 앉고 서는 동안 몇 초씩 오락가락한다.
    posture_flicker_grace_seconds: float = Field(default=2.0, ge=0, le=10, allow_inf_nan=False)
    auto_rearm_distance_cm: float = Field(default=1.5, gt=1.0, le=5.0, allow_inf_nan=False)
    auto_rearm_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    # 조명 시각 스케줄이 쓰는 현지 시간대. 컨테이너는 UTC로 도는 경우가 많아
    # 여기서 명시하지 않으면 하루가 통째로 어긋난다.
    schedule_timezone: str = "Asia/Seoul"


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
    realtime_model: str = "gpt-realtime-2.1"
    delegate_model: str = "gpt-5.6-terra"
    delegate_reasoning_effort: str = "low"

    @field_validator(
        "response_model",
        "realtime_model",
        "delegate_model",
        "delegate_reasoning_effort",
    )
    @classmethod
    def normalize_required_string(cls, value: str) -> str:
        """모델과 음성 이름의 공백을 제거하고 빈 값을 거부한다."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("OpenAI 모델과 음성 이름은 비어 있을 수 없습니다.")
        return normalized


class ProfileMemorySettings(BaseModel):
    """Optional, in-process Mem0 storage settings."""

    enabled: bool = False
    data_path: Path = Path("data/mem0")
    history_db_path: Path = Path("data/mem0/history.db")
    collection_name: str = "smart_desk_profile_memory_v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    search_limit: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=2.0, gt=0, le=10, allow_inf_nan=False)
    write_timeout_seconds: float = Field(default=8.0, gt=0, le=15, allow_inf_nan=False)
    fact_limit: int = Field(default=500, ge=1, le=2000)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    circuit_open_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)


class VoiceSettings(BaseModel):
    """로컬 microphone, Wake Word와 speaker 동작 설정을 보관한다."""

    enabled: bool = False
    input_device_name: str | None = None
    output_device_name: str | None = None

    wakeword_model_path: Path = Path(
        "assets/voice/models/hi_smarty_ko_mixed_v0_2_0.onnx"
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
    speech_start_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=15,
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
        default=1.0,
        ge=0,
        le=2.0,
        allow_inf_nan=False,
    )
    input_queue_frames: int = Field(default=64, ge=8, le=256)
    session_history_item_cap: int = Field(default=24, ge=1, le=200)

    acknowledgement_effect_path: Path = Path(
        "assets/voice/effects/acknowledgement.wav"
    )
    error_effect_path: Path = Path("assets/voice/effects/error.wav")
    realtime_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=15)
    delegate_timeout_seconds: float = Field(default=12.0, gt=0, le=30)
    realtime_episode_max_seconds: float = Field(default=120.0, gt=0, le=600)
    realtime_call_ledger_cap: int = Field(default=64, ge=1, le=256)
    realtime_voice: str = "coral"

    # 얼굴을 알아본 순간 이름을 부르고 날씨와 관심사를 전한다.
    greeting_enabled: bool = True
    # 날씨를 찾을 지역. 인사말에 그대로 쓰인다.
    greeting_location: str = "시흥"
    # 같은 사람에게 다시 인사하기까지 두는 시간(초). 작업 모드를 기억하는
    # 시간과 같은 값이라야 "같은 방문"의 기준이 어긋나지 않는다.
    greeting_cooldown_seconds: float = 1800.0
    # 마지막 인사 시각을 남길 파일. 프로세스가 다시 떠도 대기 시간이 이어진다.
    greeting_state_file: Path = Path("data/greeting_state.json")
    # 책상이 움직일 때 어디로 가는지 말해 준다.
    height_announcement_enabled: bool = True

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
    tilt: TiltSettings = TiltSettings()
    media: MediaSettings = MediaSettings()
    vision: VisionSettings = VisionSettings()
    vision_client: VisionClientSettings = VisionClientSettings()
    vision_server: VisionServerSettings = VisionServerSettings()
    face: FaceSettings = FaceSettings()
    automation: AutomationSettings = AutomationSettings()
    storage: StorageSettings = StorageSettings()
    dashboard: DashboardSettings = DashboardSettings()
    wled: WledSettings = WledSettings()
    openai: OpenAiSettings = OpenAiSettings()
    profile_memory: ProfileMemorySettings = ProfileMemorySettings()
    voice: VoiceSettings = VoiceSettings()
    voice_debug: VoiceDebugSettings = VoiceDebugSettings()

    @model_validator(mode="after")
    def validate_cross_component_settings(self) -> Settings:
        """서로 다른 subsystem 설정 사이의 필수 관계를 검증한다."""

        if self.desk.relay_ack_timeout_seconds <= self.mqtt.operation_timeout_seconds:
            raise ValueError(
                "relay ack timeout은 MQTT publish timeout보다 길어야 합니다."
            )
        if self.automation.auto_rearm_distance_cm <= self.desk.target_tolerance_cm:
            raise ValueError("AUTO 재보정 거리는 목표 허용 오차보다 커야 합니다.")
        if (self.voice.enabled or self.profile_memory.enabled) and self.openai.api_key is None:
            raise ValueError("Voice 또는 profile memory가 활성화되면 OpenAI API key가 필요합니다.")
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
