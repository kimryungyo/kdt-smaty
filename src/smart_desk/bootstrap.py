"""애플리케이션이 사용할 singleton 객체를 한곳에서 조립한다."""

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
from smart_desk.modules.desk.relay import RelayClient
from smart_desk.modules.desk.segment import SegmentDecoder
from smart_desk.modules.mqtt.client import MqttClient
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC
from smart_desk.modules.serial.source import SerialLineSource


def build_container(settings: Settings) -> AppContainer:
    """검증된 설정으로 프로세스의 공유 객체 컨테이너를 만든다."""

    runtime = RuntimeState()
    task_manager = TaskManager(
        on_critical_failure=lambda name, error: runtime.mark_failed(
            f"필수 작업 '{name}'이(가) 종료되었습니다: {error}"
        )
    )
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
    mqtt.register_handler(
        ESP32_STATUS_TOPIC,
        relay.handle_status,
        qos=0,
    )
    container = AppContainer(
        settings=settings,
        runtime=runtime,
        task_manager=task_manager,
        mqtt=mqtt,
        height_monitor=height_monitor,
        relay=relay,
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
    return container
