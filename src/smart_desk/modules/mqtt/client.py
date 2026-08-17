"""단일 EMQX 연결의 수명주기와 MQTT 메시지 전달을 관리한다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import cast

import aiomqtt

from smart_desk.config.settings import MqttSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.models import MessageHandler, MqttMessage, MqttQos


LOGGER = logging.getLogger(__name__)
MQTT_TASK_NAME = "mqtt"
VALID_QOS_VALUES = (0, 1, 2)


class MqttClientError(RuntimeError):
    """SMART DESK MQTT transport의 기본 오류."""


class MqttStartupError(MqttClientError):
    """최초 MQTT runner가 예상 밖 오류로 종료된 오류."""


class MqttUnavailableError(MqttClientError):
    """현재 MQTT 메시지를 발행할 수 없는 오류."""


class MqttClient:
    """EMQX 연결 하나를 공유하며 exact-topic handler에 메시지를 전달한다."""

    def __init__(
        self,
        settings: MqttSettings,
        task_manager: TaskManager,
    ) -> None:
        self._settings = settings
        self._task_manager = task_manager
        self._handlers: dict[str, tuple[MqttQos, MessageHandler]] = {}
        self._client: aiomqtt.Client | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._startup_event = asyncio.Event()
        self._connected = False
        self._last_error: BaseException | None = None

    def register_handler(
        self,
        topic: str,
        handler: MessageHandler,
        *,
        qos: MqttQos = 1,
    ) -> None:
        """시작 전에 exact topic 하나와 async handler를 등록한다."""

        validated_topic = self._validate_topic(topic)
        validated_qos = self._validate_qos(qos)
        if not callable(handler):
            raise TypeError("MQTT handler는 호출 가능한 객체여야 합니다.")
        if self._runner_task is not None and not self._runner_task.done():
            raise RuntimeError("MQTT client 시작 후에는 handler를 등록할 수 없습니다.")
        if validated_topic in self._handlers:
            raise RuntimeError(f"MQTT 토픽 '{validated_topic}' handler가 이미 등록되었습니다.")
        self._handlers[validated_topic] = (validated_qos, handler)

    async def start(self) -> None:
        """첫 연결을 기다리되 broker가 없으면 재연결 runner를 남긴다."""

        if self._runner_task is not None and not self._runner_task.done():
            raise RuntimeError("MQTT client가 이미 실행 중입니다.")

        self._startup_event.clear()
        self._connected = False
        self._last_error = None
        self._runner_task = self._task_manager.create(
            MQTT_TASK_NAME,
            self._run(),
            critical=True,
        )

        try:
            async with asyncio.timeout(self._settings.operation_timeout_seconds):
                await self._startup_event.wait()
        except TimeoutError:
            # Broker cold-start는 lifecycle을 막지 않는다. runner는 연결을 기다린다.
            return

        if self._connected:
            return

        if isinstance(self._last_error, aiomqtt.MqttError):
            # 최초 connect/subscribe 실패도 runtime disconnect와 같은 transient 오류다.
            return

        startup_error = self._last_error
        if startup_error is not None:
            await self._cancel_runner()
            raise MqttStartupError("MQTT 최초 연결과 구독에 실패했습니다.") from startup_error

    async def stop(self) -> None:
        """수신·재연결 runner와 현재 broker 연결을 안전하게 종료한다."""

        had_runner = self._runner_task is not None
        await self._cancel_runner()
        self._connected = False
        self._client = None
        if had_runner:
            LOGGER.info(
                "MQTT client를 종료했습니다.",
                extra={"component": "mqtt", "event": "mqtt_stopped"},
            )

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: MqttQos = 1,
        retain: bool = False,
    ) -> None:
        """현재 연결로 메시지를 발행하고 broker 확인까지 기다린다."""

        validated_topic = self._validate_topic(topic)
        validated_qos = self._validate_qos(qos)
        if not isinstance(payload, (bytes, str)):
            raise TypeError("MQTT payload는 bytes 또는 str이어야 합니다.")

        client = self._client
        if not self._connected or client is None:
            raise MqttUnavailableError("MQTT broker에 연결되지 않아 발행할 수 없습니다.")

        try:
            await client.publish(
                validated_topic,
                payload,
                qos=validated_qos,
                retain=retain,
                timeout=self._settings.operation_timeout_seconds,
            )
        except (aiomqtt.MqttError, TimeoutError) as error:
            raise MqttUnavailableError("MQTT 메시지 발행에 실패했습니다.") from error

    def is_connected(self) -> bool:
        """네트워크 I/O 없이 현재 연결·구독 완료 여부를 반환한다."""

        return self._connected

    async def _run(self) -> None:
        """메시지를 수신하고 최초 성공 이후의 연결 단절을 복구한다."""

        startup_observed = False
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._settings.host,
                    port=self._settings.port,
                    identifier=self._settings.client_id,
                    protocol=aiomqtt.ProtocolVersion.V311,
                    clean_session=True,
                    keepalive=self._settings.keepalive_seconds,
                    timeout=self._settings.operation_timeout_seconds,
                ) as client:
                    self._client = client
                    await self._subscribe_all(client)
                    self._connected = True
                    self._last_error = None
                    if not startup_observed:
                        startup_observed = True
                        self._startup_event.set()
                    LOGGER.info(
                        "MQTT broker 연결과 구독을 완료했습니다.",
                        extra={"component": "mqtt", "event": "mqtt_connected"},
                    )

                    async for message in client.messages:
                        await self._dispatch(message)

                    raise aiomqtt.MqttError("MQTT 메시지 수신이 종료되었습니다.")
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as error:
                self._last_error = error
                if not startup_observed:
                    startup_observed = True
                    self._startup_event.set()
                LOGGER.warning(
                    "MQTT 연결이 끊어졌습니다.",
                    extra={"component": "mqtt", "event": "mqtt_disconnected"},
                )
            except Exception as error:
                self._last_error = error
                if not startup_observed:
                    self._startup_event.set()
                raise
            finally:
                self._connected = False
                self._client = None

            LOGGER.info(
                "MQTT broker 재연결을 기다립니다.",
                extra={"component": "mqtt", "event": "mqtt_reconnect_wait"},
            )
            await asyncio.sleep(self._settings.reconnect_interval_seconds)

    async def _subscribe_all(self, client: aiomqtt.Client) -> None:
        """현재 연결에서 등록된 모든 exact topic을 구독한다."""

        for topic, (qos, _handler) in self._handlers.items():
            await client.subscribe(
                topic,
                qos=qos,
                timeout=self._settings.operation_timeout_seconds,
            )

    async def _dispatch(self, message: aiomqtt.Message) -> None:
        """수신 메시지를 공개 타입으로 변환해 등록 handler에 전달한다."""

        topic = message.topic.value
        registration = self._handlers.get(topic)
        if registration is None:
            LOGGER.debug(
                "등록되지 않은 MQTT 토픽 메시지를 무시했습니다.",
                extra={"component": "mqtt", "event": "mqtt_message_ignored"},
            )
            return

        _qos, handler = registration
        mqtt_message = MqttMessage(
            topic=topic,
            payload=bytes(message.payload),
            qos=self._validate_qos(int(message.qos)),
            retained=bool(message.retain),
            received_at=datetime.now(UTC),
        )
        try:
            await handler(mqtt_message)
        except Exception:
            LOGGER.exception(
                "MQTT 메시지 handler 실행에 실패했습니다.",
                extra={"component": "mqtt", "event": "mqtt_handler_failed"},
            )

    async def _cancel_runner(self) -> None:
        """runner를 취소하고 종료가 끝날 때까지 기다린다."""

        task = self._runner_task
        self._runner_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _validate_topic(topic: str) -> str:
        if not isinstance(topic, str):
            raise TypeError("MQTT topic은 str이어야 합니다.")
        if not topic.strip():
            raise ValueError("MQTT topic은 비어 있을 수 없습니다.")
        if "+" in topic or "#" in topic:
            raise ValueError("현재 MQTT 모듈은 wildcard topic을 지원하지 않습니다.")
        return topic

    @staticmethod
    def _validate_qos(qos: int) -> MqttQos:
        if not isinstance(qos, int) or isinstance(qos, bool) or qos not in VALID_QOS_VALUES:
            raise ValueError("MQTT QoS는 0, 1, 2 중 하나여야 합니다.")
        return cast("MqttQos", qos)
