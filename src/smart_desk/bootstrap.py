"""애플리케이션이 사용할 singleton 객체를 한곳에서 조립한다."""

import logging

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard.service import DashboardService
from smart_desk.modules.desk.controller import DeskController
from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
from smart_desk.modules.desk.relay import RelayClient
from smart_desk.modules.desk.segment import SegmentDecoder
from smart_desk.modules.mqtt.client import MqttClient
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC
from smart_desk.modules.media import CameraPublisher, RtspFrameSource
from smart_desk.modules.profiles.repository import ProfileRepository
from smart_desk.modules.serial.source import SerialLineSource
from smart_desk.modules.wled.client import WledClient
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
    mqtt = MqttClient(settings.mqtt, task_manager)
    serial_source = SerialLineSource(settings.serial)
    decoder = SegmentDecoder(settings.desk)
    relay = RelayClient(mqtt)
    height_monitor = DeskHeightMonitor(
        serial_source,
        decoder,
        mqtt,
        settings.desk,
        task_manager,
    )
    desk = DeskController(
        height_monitor,
        relay,
        settings.desk,
        task_manager,
    )
    dashboard = DashboardService(desk, profiles)
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
        dashboard=dashboard,
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
    if settings.vision.enabled:
        container.register(
            ResourceRegistration(
                name="camera-publisher-user",
                resource=CameraPublisher(
                    name="user",
                    device=settings.vision.user_camera_device,
                    rtsp_url=settings.vision.user_rtsp_url,
                    ffmpeg_path=settings.vision.ffmpeg_path,
                    input_format=settings.vision.user_input_format,
                    width=settings.vision.user_width,
                    height=settings.vision.user_height,
                    fps=settings.vision.user_fps,
                ),
                startup_order=40,
                shutdown_order=40,
            )
        )
        container.register(
            ResourceRegistration(
                name="camera-publisher-posture",
                resource=CameraPublisher(
                    name="posture",
                    device=settings.vision.posture_camera_device,
                    rtsp_url=settings.vision.posture_rtsp_url,
                    ffmpeg_path=settings.vision.ffmpeg_path,
                    input_format=settings.vision.posture_input_format,
                    width=settings.vision.posture_width,
                    height=settings.vision.posture_height,
                    fps=settings.vision.posture_fps,
                ),
                startup_order=41,
                shutdown_order=41,
            )
        )
        container.register(
            ResourceRegistration(
                name="rtsp-frame-source-user",
                resource=RtspFrameSource(
                    name="user",
                    rtsp_url=settings.vision.user_rtsp_url,
                    reconnect_interval_seconds=(
                        settings.vision.rtsp_reconnect_interval_seconds
                    ),
                ),
                startup_order=50,
                shutdown_order=50,
            )
        )
        container.register(
            ResourceRegistration(
                name="rtsp-frame-source-posture",
                resource=RtspFrameSource(
                    name="posture",
                    rtsp_url=settings.vision.posture_rtsp_url,
                    reconnect_interval_seconds=(
                        settings.vision.rtsp_reconnect_interval_seconds
                    ),
                ),
                startup_order=51,
                shutdown_order=51,
            )
        )
    if settings.wled.enabled:
        wled = WledClient(settings.wled)
        container.wled = wled
        container.register(
            ResourceRegistration(
                name="wled",
                resource=wled,
                startup_order=60,
                shutdown_order=60,
            )
        )
    if settings.voice.enabled:
        try:
            from smart_desk.modules.assistant.openai import OpenAiGateway
            from smart_desk.modules.assistant.service import AssistantService
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
            assistant = AssistantService(
                gateway,
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
