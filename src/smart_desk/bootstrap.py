"""애플리케이션이 사용할 singleton 객체를 한곳에서 조립한다."""

import logging

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard.service import DashboardService
from smart_desk.modules.desk.controller import DeskController
from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
from smart_desk.modules.desk.height_cache import HeightCacheRepository
from smart_desk.modules.desk.relay import RelayClient
from smart_desk.modules.desk.segment import SegmentDecoder
from smart_desk.modules.mqtt.client import MqttClient
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC
from smart_desk.modules.media import CameraPublisher, RtspFrameSource
from smart_desk.modules.profiles.repository import ProfileRepository
from smart_desk.modules.profiles.activity_modes import ActivityModeRepository
from smart_desk.modules.serial.source import SerialLineSource
from smart_desk.modules.wled.client import WledClient
from smart_desk.modules.vision import NoopVisionDetector, VisionService
from smart_desk.modules.identity import FaceIdentityService, UnavailableFaceEmbeddingExtractor
from smart_desk.modules.identity.repository import FaceEmbeddingRepository
from smart_desk.modules.identity.session import CurrentUserSessionService
from smart_desk.modules.automation.service import AutomationService
from smart_desk.storage import SQLiteDatabase


LOGGER = logging.getLogger(__name__)


def build_container(settings: Settings) -> AppContainer:
    """검증된 설정으로 프로세스의 공유 객체 컨테이너를 만든다."""

    runtime = RuntimeState()
    task_manager = TaskManager(
        on_critical_failure=lambda name, error: runtime.mark_failed(
            f"필수 작업 '{name}'이(가) 종료되었습니다: {error}"
        )
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    mqtt = MqttClient(settings.mqtt, task_manager)
    serial_source = SerialLineSource(settings.serial)
    decoder = SegmentDecoder(settings.desk)
    relay = RelayClient(mqtt)
    height_cache = HeightCacheRepository(database)
    height_monitor = DeskHeightMonitor(
        serial_source,
        decoder,
        mqtt,
        settings.desk,
        task_manager,
        cache=height_cache,
    )
    desk = DeskController(
        height_monitor,
        relay,
        settings.desk,
        task_manager,
    )
    mqtt.register_handler(
        ESP32_STATUS_TOPIC,
        relay.handle_status,
        qos=0,
    )
    container = AppContainer(
        settings=settings,
        runtime=runtime,
        task_manager=task_manager,
        database=database,
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=DashboardService(desk, profiles),
        mqtt=mqtt,
        height_monitor=height_monitor,
        relay=relay,
        desk=desk,
    )
    container.register(
        ResourceRegistration(
            name="sqlite",
            resource=database,
            startup_order=5,
            shutdown_order=5,
        )
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk-height-monitor",
            resource=height_monitor,
            startup_order=20,
            shutdown_order=20,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk-controller",
            resource=desk,
            startup_order=30,
            shutdown_order=30,
        )
    )
    if settings.media.user.publish_enabled:
        user_camera_publisher = CameraPublisher(
            name="user",
            device=settings.media.user.device,
            rtsp_url=settings.media.user.publish_url,
            ffmpeg_path=settings.media.ffmpeg_path,
            input_format=settings.media.user.input_format,
            width=settings.media.user.width,
            height=settings.media.user.height,
            fps=settings.media.user.fps,
        )
        container.user_camera_publisher = user_camera_publisher
        container.register(
            ResourceRegistration(
                name="camera-publisher-user",
                resource=user_camera_publisher,
                startup_order=40,
                shutdown_order=40,
            )
        )
    if settings.media.posture.publish_enabled:
        posture_camera_publisher = CameraPublisher(
            name="posture",
            device=settings.media.posture.device,
            rtsp_url=settings.media.posture.publish_url,
            ffmpeg_path=settings.media.ffmpeg_path,
            input_format=settings.media.posture.input_format,
            width=settings.media.posture.width,
            height=settings.media.posture.height,
            fps=settings.media.posture.fps,
        )
        container.posture_camera_publisher = posture_camera_publisher
        container.register(
            ResourceRegistration(
                name="camera-publisher-posture",
                resource=posture_camera_publisher,
                startup_order=41,
                shutdown_order=41,
            )
        )
    if settings.media.workspace.publish_enabled:
        workspace_camera_publisher = CameraPublisher(
            name="workspace",
            device=settings.media.workspace.device,
            rtsp_url=settings.media.workspace.publish_url,
            ffmpeg_path=settings.media.ffmpeg_path,
            input_format=settings.media.workspace.input_format,
            width=settings.media.workspace.width,
            height=settings.media.workspace.height,
            fps=settings.media.workspace.fps,
        )
        container.workspace_camera_publisher = workspace_camera_publisher
        container.register(
            ResourceRegistration(
                name="camera-publisher-workspace",
                resource=workspace_camera_publisher,
                startup_order=42,
                shutdown_order=42,
            )
        )
    if settings.media.user.receive_enabled:
        user_frame_source = RtspFrameSource(
            name="user",
            rtsp_url=settings.media.user.receive_url,
            reconnect_interval_seconds=(
                settings.media.rtsp_reconnect_interval_seconds
            ),
        )
        container.user_frame_source = user_frame_source
        container.register(
            ResourceRegistration(
                name="rtsp-frame-source-user",
                resource=user_frame_source,
                startup_order=50,
                shutdown_order=50,
            )
        )
    if settings.media.posture.receive_enabled:
        posture_frame_source = RtspFrameSource(
            name="posture",
            rtsp_url=settings.media.posture.receive_url,
            reconnect_interval_seconds=(
                settings.media.rtsp_reconnect_interval_seconds
            ),
        )
        container.posture_frame_source = posture_frame_source
        container.register(
            ResourceRegistration(
                name="rtsp-frame-source-posture",
                resource=posture_frame_source,
                startup_order=51,
                shutdown_order=51,
            )
        )
    if settings.media.workspace.receive_enabled:
        workspace_frame_source = RtspFrameSource(
            name="workspace",
            rtsp_url=settings.media.workspace.receive_url,
            reconnect_interval_seconds=(
                settings.media.rtsp_reconnect_interval_seconds
            ),
        )
        container.workspace_frame_source = workspace_frame_source
        container.register(
            ResourceRegistration(
                name="rtsp-frame-source-workspace",
                resource=workspace_frame_source,
                startup_order=52,
                shutdown_order=52,
            )
        )
    # Vision은 user(상단)와 posture(하단)만 소비한다. workspace 영상은 AI 작업공간
    # 역할이므로 편의상 자세 입력으로 대체하지 않는다.
    vision = VisionService(
        upper_source=container.user_frame_source,
        lower_source=container.posture_frame_source,
        detector=NoopVisionDetector(),
        settings=settings.vision,
    )
    container.vision = vision
    container.register(
        ResourceRegistration(
            name="vision",
            resource=vision,
            startup_order=60,
            shutdown_order=60,
        )
    )
    face_embeddings = FaceEmbeddingRepository(database)
    current_user = CurrentUserSessionService()
    from smart_desk.modules.assistant.memory import ProfileMemoryService
    from smart_desk.modules.assistant.context import CurrentUserSessionManager
    from smart_desk.modules.assistant.turns import AssistantTurnStore
    memory_config: dict[str, object] = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "smart_desk_profile_memory",
                "path": str(settings.profile_memory.data_path / "qdrant"),
            },
        },
        "history_db_path": str(settings.profile_memory.history_db_path),
    }
    if settings.profile_memory.enabled:
        api_key = settings.openai.api_key
        assert api_key is not None  # Settings validates this dependency at startup.
        llm_config = {
            "api_key": api_key.get_secret_value(),
            "model": settings.openai.response_model,
        }
        embedder_config = {
            "api_key": api_key.get_secret_value(),
            "model": "text-embedding-3-small",
        }
        memory_config["llm"] = {"provider": "openai", "config": llm_config}
        memory_config["embedder"] = {"provider": "openai", "config": embedder_config}
    container.profile_memory = ProfileMemoryService(
        enabled=settings.profile_memory.enabled,
        config=memory_config,
        search_limit=settings.profile_memory.search_limit,
        timeout_seconds=settings.profile_memory.timeout_seconds,
    )
    container.assistant_context = CurrentUserSessionManager(
        current_user, item_cap=settings.voice.session_history_item_cap
    )
    container.assistant_turns = AssistantTurnStore(current_user)
    identity = FaceIdentityService(vision=vision, repository=face_embeddings,
                                   current_user=current_user,
                                   extractor=UnavailableFaceEmbeddingExtractor())
    container.face_embeddings = face_embeddings
    container.current_user = current_user
    container.identity = identity
    container.register(ResourceRegistration(name="face-identity", resource=identity,
                                            startup_order=70, shutdown_order=70))
    container.register(ResourceRegistration(name="assistant-context", resource=container.assistant_context,
                                            startup_order=75, shutdown_order=75))
    container.register(ResourceRegistration(name="assistant-turns", resource=container.assistant_turns,
                                            startup_order=76, shutdown_order=76))
    if settings.wled.enabled:
        wled = WledClient(settings.wled, session_validator=current_user.is_current)
        container.wled = wled
        container.register(
            ResourceRegistration(
                name="wled",
                resource=wled,
                startup_order=60,
                shutdown_order=60,
            )
        )
    automation = AutomationService(
        current_user=current_user, vision=vision, activity_modes=activity_modes,
        desk=desk, settings=settings.automation, wled=container.wled,
        target_tolerance_cm=settings.desk.target_tolerance_cm,
    )
    container.automation = automation
    container.dashboard = DashboardService(desk, profiles, automation)
    container.register(ResourceRegistration(name="desk-automation", resource=automation,
                                            startup_order=80, shutdown_order=80))
    if settings.voice.enabled:
        try:
            from smart_desk.modules.assistant.openai import OpenAiGateway
            from smart_desk.modules.assistant.service import AssistantService
            from smart_desk.modules.assistant.tooling import AssistantToolRegistry
            from smart_desk.modules.assistant.wled_tools import WledAssistantTools
            from smart_desk.modules.voice.audio import (
                LocalAudioInput,
                LocalPcmOutput,
                RmsRecorder,
            )
            from smart_desk.modules.voice.debug import VoiceDebugServer, VoiceDebugView
            from smart_desk.modules.voice.playback import PlaybackCoordinator
            from smart_desk.modules.voice.service import VoiceService
            from smart_desk.modules.voice.wakeword import LiveKitWakeWordOnnxDetector

            gateway = OpenAiGateway(settings.openai)
            tool_registry = AssistantToolRegistry(
                (WledAssistantTools(container.wled),)
                if container.wled is not None
                else ()
            )
            assistant = AssistantService(
                gateway,
                tool_registry,
                session_max_turns=settings.voice.session_max_turns,
            )
            audio_input = LocalAudioInput(
                device_name=settings.voice.input_device_name,
                queue_frames=settings.voice.input_queue_frames,
            )
            output = LocalPcmOutput(device_name=settings.voice.output_device_name)
            playback = PlaybackCoordinator(
                output,
                acknowledgement_effect_path=(
                    settings.voice.acknowledgement_effect_path
                ),
                error_effect_path=settings.voice.error_effect_path,
            )
            wakeword = LiveKitWakeWordOnnxDetector(
                model_path=settings.voice.wakeword_model_path,
                threshold=settings.voice.wakeword_threshold,
                consecutive_frames=settings.voice.wakeword_consecutive_frames,
                inference_interval_frames=(
                    settings.voice.wakeword_inference_interval_frames
                ),
            )
            recorder = RmsRecorder(
                rms_threshold=settings.voice.silence_rms_threshold,
                speech_start_consecutive_frames=(
                    settings.voice.speech_start_consecutive_frames
                ),
                silence_duration_seconds=settings.voice.silence_duration_seconds,
                min_utterance_seconds=settings.voice.min_utterance_seconds,
                max_utterance_seconds=settings.voice.max_utterance_seconds,
                preroll_seconds=settings.voice.followup_preroll_seconds,
            )
            voice = VoiceService(
                audio_input=audio_input,
                wakeword=wakeword,
                recorder=recorder,
                gateway=gateway,
                assistant=assistant,
                playback=playback,
                settings=settings.voice,
                task_manager=task_manager,
            )
        except Exception:
            LOGGER.error(
                "Voice dependency를 초기화하지 못했습니다.",
                extra={
                    "component": "voice",
                    "event": "voice_build_failed",
                    "error_code": "voice_dependency_missing",
                },
            )
        else:
            container.assistant = assistant
            container.voice = voice
            container.register(
                ResourceRegistration(
                    name="voice",
                    resource=voice,
                    startup_order=70,
                    shutdown_order=70,
                )
            )
            if settings.voice_debug.enabled:
                voice_debug = VoiceDebugServer(
                    VoiceDebugView(
                        voice=voice,
                        wakeword=wakeword,
                        audio_input=audio_input,
                        assistant=assistant,
                    ),
                    settings.voice_debug,
                    task_manager,
                )
                container.voice_debug = voice_debug
                container.register(
                    ResourceRegistration(
                        name="voice-debug-http",
                        resource=voice_debug,
                        startup_order=80,
                        shutdown_order=80,
                    )
                )
    return container
