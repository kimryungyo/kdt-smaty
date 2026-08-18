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
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC, TILT_COMMAND_TOPIC
from smart_desk.modules.media import WebRtcCameraPublisher, WebRtcFrameSource
from smart_desk.modules.profiles.repository import ProfileRepository
from smart_desk.modules.profiles.activity_modes import ActivityModeRepository
from smart_desk.modules.profiles.usage import ActivityModeUsageRepository
from smart_desk.modules.serial.source import SerialLineSource
from smart_desk.modules.tilt.controller import TiltController
from smart_desk.modules.tilt.level_repository import TiltLevelRepository
from smart_desk.modules.tilt.serial_link import TiltSerialLink
from smart_desk.modules.wled.client import WledClient
from smart_desk.modules.vision import (
    CompositeVisionDetector,
    PresenceAndFaceUpperDetector,
    OpenCvYuNetUpperDetector,
    OpenCvYoloPoseLowerDetector,
    NoopVisionDetector,
    VisionService,
)
from smart_desk.modules.vision.remote import RemoteFaceEmbeddingExtractor, RemoteVisionService
from smart_desk.modules.identity import (
    FaceIdentityService, OpenCvSFaceEmbeddingExtractor, UnavailableFaceEmbeddingExtractor,
)
from smart_desk.modules.identity.service import FaceRecognizer
from smart_desk.modules.identity.repository import FaceEmbeddingRepository
from smart_desk.modules.identity.session import CurrentUserSessionService
from smart_desk.modules.automation.service import AutomationService
from smart_desk.storage import SQLiteDatabase


LOGGER = logging.getLogger(__name__)


def _build_local_vision(settings: Settings, container: AppContainer) -> VisionService:
    """Keep the legacy in-process Vision path for non-Docker development."""

    face_detector = NoopVisionDetector()
    upper_presence_detector = NoopVisionDetector()
    lower_detector = NoopVisionDetector()
    if settings.face.detector_model_path is not None:
        try:
            face_detector = OpenCvYuNetUpperDetector(
                settings.face.detector_model_path,
                score_threshold=settings.face.detector_score_threshold,
                nms_threshold=settings.face.detector_nms_threshold,
                min_face_size=settings.face.min_face_size,
            )
        except Exception:
            LOGGER.exception("Failed to load YuNet face model; face identification is unavailable")
    if settings.vision.lower_pose_model_path is not None:
        try:
            upper_presence_detector = OpenCvYoloPoseLowerDetector(
                settings.vision.lower_pose_model_path,
                input_size=settings.vision.lower_pose_input_size,
                min_person_confidence=settings.vision.upper_presence_min_person_confidence,
                min_hip_confidence=settings.vision.lower_pose_min_hip_confidence,
                min_knee_ankle_confidence=settings.vision.lower_pose_min_knee_ankle_confidence,
                decision_threshold=settings.vision.lower_pose_decision_threshold,
            )
            lower_detector = OpenCvYoloPoseLowerDetector(
                settings.vision.lower_pose_model_path,
                input_size=settings.vision.lower_pose_input_size,
                min_person_confidence=settings.vision.lower_pose_min_person_confidence,
                min_hip_confidence=settings.vision.lower_pose_min_hip_confidence,
                min_knee_ankle_confidence=settings.vision.lower_pose_min_knee_ankle_confidence,
                decision_threshold=settings.vision.lower_pose_decision_threshold,
            )
        except Exception:
            LOGGER.exception("Failed to load YOLO pose model; Vision is unavailable")
    if not isinstance(upper_presence_detector, NoopVisionDetector) and not isinstance(face_detector, NoopVisionDetector):
        upper_detector = PresenceAndFaceUpperDetector(upper_presence_detector, face_detector)
    elif not isinstance(upper_presence_detector, NoopVisionDetector):
        upper_detector = upper_presence_detector
    else:
        upper_detector = face_detector
    if not isinstance(upper_detector, NoopVisionDetector) and not isinstance(lower_detector, NoopVisionDetector):
        detector = CompositeVisionDetector(upper_detector, lower_detector)
    elif not isinstance(upper_detector, NoopVisionDetector):
        detector = upper_detector
    elif not isinstance(lower_detector, NoopVisionDetector):
        detector = lower_detector
    else:
        detector = NoopVisionDetector()
    return VisionService(
        upper_source=container.user_frame_source,
        lower_source=container.posture_frame_source,
        detector=detector,
        settings=settings.vision,
    )


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
    mode_usage = ActivityModeUsageRepository(database)
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
        mode_usage=mode_usage,
        dashboard=DashboardService(desk, profiles, activity_modes=activity_modes),
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
    if settings.tilt.enabled:
        tilt_levels = TiltLevelRepository(
            settings.tilt.levels_file, settings.tilt.calibration_file
        )
        tilt_levels.validate_for(
            min_level=settings.tilt.min_level,
            max_level=settings.tilt.max_level,
            move_duty_percent=settings.tilt.move_duty_percent,
        )
        tilt_link = TiltSerialLink(settings.tilt)
        tilt = TiltController(tilt_link, tilt_levels, mqtt, settings.tilt, task_manager)
        container.tilt = tilt
        mqtt.register_handler(
            TILT_COMMAND_TOPIC,
            tilt.handle_command,
            qos=1,
        )
        container.register(
            ResourceRegistration(
                name="tilt",
                resource=tilt,
                startup_order=35,
                shutdown_order=35,
            )
        )
    if settings.media.user.publish_enabled:
        user_camera_publisher = WebRtcCameraPublisher(
            name="user",
            device=settings.media.user.device,
            whip_url=settings.media.user.publish_url,
            input_format=settings.media.user.input_format,
            width=settings.media.user.width,
            height=settings.media.user.height,
            fps=settings.media.user.fps,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.user_camera_publisher = user_camera_publisher
        container.register(
            ResourceRegistration(
                name="webrtc-camera-publisher-user",
                resource=user_camera_publisher,
                startup_order=40,
                shutdown_order=40,
            )
        )
    if settings.media.posture.publish_enabled:
        posture_camera_publisher = WebRtcCameraPublisher(
            name="posture",
            device=settings.media.posture.device,
            whip_url=settings.media.posture.publish_url,
            input_format=settings.media.posture.input_format,
            width=settings.media.posture.width,
            height=settings.media.posture.height,
            fps=settings.media.posture.fps,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.posture_camera_publisher = posture_camera_publisher
        container.register(
            ResourceRegistration(
                name="webrtc-camera-publisher-posture",
                resource=posture_camera_publisher,
                startup_order=41,
                shutdown_order=41,
            )
        )
    if settings.media.workspace.publish_enabled:
        workspace_camera_publisher = WebRtcCameraPublisher(
            name="workspace",
            device=settings.media.workspace.device,
            whip_url=settings.media.workspace.publish_url,
            input_format=settings.media.workspace.input_format,
            width=settings.media.workspace.width,
            height=settings.media.workspace.height,
            fps=settings.media.workspace.fps,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.workspace_camera_publisher = workspace_camera_publisher
        container.register(
            ResourceRegistration(
                name="webrtc-camera-publisher-workspace",
                resource=workspace_camera_publisher,
                startup_order=42,
                shutdown_order=42,
            )
        )
    if not settings.vision_client.enabled and settings.media.user.receive_enabled:
        user_frame_source = WebRtcFrameSource(
            name="user",
            whep_url=settings.media.user.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.user_frame_source = user_frame_source
        container.register(
            ResourceRegistration(
                name="webrtc-frame-source-user",
                resource=user_frame_source,
                startup_order=50,
                shutdown_order=50,
            )
        )
    if not settings.vision_client.enabled and settings.media.posture.receive_enabled:
        posture_frame_source = WebRtcFrameSource(
            name="posture",
            whep_url=settings.media.posture.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.posture_frame_source = posture_frame_source
        container.register(
            ResourceRegistration(
                name="webrtc-frame-source-posture",
                resource=posture_frame_source,
                startup_order=51,
                shutdown_order=51,
            )
        )
    if not settings.vision_client.enabled and settings.media.workspace.receive_enabled:
        workspace_frame_source = WebRtcFrameSource(
            name="workspace",
            whep_url=settings.media.workspace.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        container.workspace_frame_source = workspace_frame_source
        container.register(
            ResourceRegistration(
                name="webrtc-frame-source-workspace",
                resource=workspace_frame_source,
                startup_order=52,
                shutdown_order=52,
            )
        )
    if settings.vision_client.enabled:
        vision = RemoteVisionService(settings.vision_client)
    else:
        vision = _build_local_vision(settings, container)
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
                "collection_name": settings.profile_memory.collection_name,
                "path": str(settings.profile_memory.data_path / "qdrant"),
                "embedding_model_dims": settings.profile_memory.embedding_dimensions,
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
            "model": settings.profile_memory.embedding_model,
            "embedding_dims": settings.profile_memory.embedding_dimensions,
        }
        memory_config["llm"] = {"provider": "openai", "config": llm_config}
        memory_config["embedder"] = {"provider": "openai", "config": embedder_config}
    container.profile_memory = ProfileMemoryService(
        enabled=settings.profile_memory.enabled,
        config=memory_config,
        search_limit=settings.profile_memory.search_limit,
        timeout_seconds=settings.profile_memory.timeout_seconds,
        write_timeout_seconds=settings.profile_memory.write_timeout_seconds,
        fact_limit=settings.profile_memory.fact_limit,
        circuit_failure_threshold=settings.profile_memory.circuit_failure_threshold,
        circuit_open_seconds=settings.profile_memory.circuit_open_seconds,
    )
    container.assistant_context = CurrentUserSessionManager(
        current_user, item_cap=settings.voice.session_history_item_cap
    )
    container.assistant_turns = AssistantTurnStore(current_user)
    extractor = UnavailableFaceEmbeddingExtractor()
    if settings.vision_client.enabled:
        extractor = RemoteFaceEmbeddingExtractor()
    elif settings.face.embedding_model_path is not None:
        try:
            extractor = OpenCvSFaceEmbeddingExtractor(
                settings.face.embedding_model_path, min_face_size=settings.face.min_face_size,
                min_blur_variance=settings.face.min_blur_variance,
                min_brightness=settings.face.min_brightness,
                max_brightness=settings.face.max_brightness,
            )
        except Exception:
            LOGGER.exception("Failed to load SFace model; using unavailable embedding extractor")
    identity = FaceIdentityService(
        vision=vision, repository=face_embeddings, current_user=current_user, extractor=extractor,
        recognizer=FaceRecognizer(match_threshold=settings.face.match_threshold,
                                  margin=settings.face.best_second_margin),
        pairwise_consistency_threshold=settings.face.pairwise_consistency_threshold,
        duplicate_threshold=settings.face.duplicate_threshold,
        enrollment_sample_interval_seconds=settings.face.enrollment_sample_interval_seconds,
    )
    container.face_embeddings = face_embeddings
    container.current_user = current_user
    container.identity = identity
    container.register(ResourceRegistration(name="face-identity", resource=identity,
                                            startup_order=70, shutdown_order=70))
    container.register(ResourceRegistration(name="profile-memory", resource=container.profile_memory,
                                            startup_order=72, shutdown_order=72))
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
        usage=mode_usage,
    )
    container.automation = automation
    container.dashboard = DashboardService(desk, profiles, automation, activity_modes=activity_modes)
    container.register(ResourceRegistration(name="desk-automation", resource=automation,
                                            startup_order=80, shutdown_order=80))
    if settings.voice.enabled:
        try:
            from smart_desk.modules.assistant.agents_runtime import (
                AgentsVoiceConfig,
                AgentsVoiceRuntime,
            )
            from smart_desk.modules.voice.audio import LocalAudioInput, LocalPcmOutput
            from smart_desk.modules.voice.debug import VoiceDebugServer, VoiceDebugView
            from smart_desk.modules.voice.playback import PlaybackCoordinator
            from smart_desk.modules.voice.service import VoiceService
            from smart_desk.modules.voice.wakeword import LiveKitWakeWordOnnxDetector

            api_key = settings.openai.api_key
            assert api_key is not None  # Settings validates enabled Voice/OpenAI.
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
            # Keep local construction ahead of the OpenAI client.  A bad local
            # device/model/effect configuration then cannot leak a runtime client.
            runtime = AgentsVoiceRuntime.build_for_services(
                api_key=api_key.get_secret_value(), sessions=container.assistant_context,
                memory=container.profile_memory, turns=container.assistant_turns,
                automation=automation, wled=container.wled,
                config=AgentsVoiceConfig(model=settings.openai.response_model),
            )
            voice = VoiceService(
                audio_input=audio_input,
                wakeword=wakeword,
                runtime=runtime,
                playback=playback,
                settings=settings.voice,
                task_manager=task_manager,
            )
        except Exception as error:
            LOGGER.exception(
                "Voice resource를 조립하지 못했습니다.",
                extra={
                    "component": "voice",
                    "event": "voice_build_failed",
                    "error_code": "voice_dependency_missing",
                },
            )
            raise RuntimeError("Voice resource 'voice' 구성에 실패했습니다.") from error
        else:
            container.voice = voice
            container.register(
                ResourceRegistration(
                    name="voice",
                    resource=voice,
                    startup_order=90,
                    shutdown_order=90,
                )
            )
            if settings.voice_debug.enabled:
                voice_debug = VoiceDebugServer(
                    VoiceDebugView(
                        voice=voice,
                        wakeword=wakeword,
                        audio_input=audio_input,
                        assistant_turns=container.assistant_turns,
                    ),
                    settings.voice_debug,
                    task_manager,
                )
                container.voice_debug = voice_debug
                container.register(
                    ResourceRegistration(
                        name="voice-debug-http",
                        resource=voice_debug,
                        startup_order=91,
                        shutdown_order=91,
                    )
                )
    return container
