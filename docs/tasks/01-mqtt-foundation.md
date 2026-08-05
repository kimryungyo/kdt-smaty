# 01. MQTT 기반

## 목표

인증 없는 로컬 EMQX `127.0.0.1:1883`에 연결하는 단일 `MqttClient`를 만들고,
이후 ESP32·Desk·WLED 모듈이 broker 라이브러리를 직접 사용하지 않게 한다.

## 작업 목록

- [x] `aiomqtt` 런타임 의존성을 추가하고 버전 범위를 고정한다.
- [x] `MqttSettings`에 `client_id`, 연결 timeout, 재연결 간격을 추가한다.
- [x] `modules/mqtt/models.py`에 `MqttMessage`와 QoS 타입을 작성한다.
- [x] `modules/mqtt/client.py`에 `start()`, `stop()`, `publish()`,
  `register_handler()`, `is_connected()`를 구현한다.
- [x] `modules/mqtt/topics.py`로 기존 토픽 문자열을 한곳에 이식한다.
- [x] MQTT 3.1.1과 `clean_session=True`를 명시하고, 연결마다 전체 토픽을 다시 구독한다.
- [x] 연결 후 등록 토픽 구독, 연결 단절 후 재연결·재구독을 구현한다.
- [x] 연결되지 않은 publish가 무한 대기하지 않고 명시적 예외를 내도록 한다.
- [x] retained 메시지를 폐기하지 않고 `MqttMessage.retained`로 전달하되, 명령
  발행의 `retain` 기본값은 `False`로 둔다.
- [x] `AppContainer.mqtt`, `get_mqtt()`와 lifecycle 등록을 추가한다.
- [x] `.env.example`과 설정 문서를 갱신한다. 인증 필드는 추가하지 않는다.

## 테스트

- [x] 가짜 client로 handler 등록, QoS와 retain 전달을 단위 테스트한다.
- [x] retained 수신 여부가 `MqttMessage.retained`에 전달되는지 검증한다.
- [x] 재연결 시 handler가 중복 실행되지 않는지 검증한다.
- [x] FastAPI lifespan에서 MQTT가 한 번 시작되고 한 번 종료되는지 검증한다.
- [x] 실제 EMQX에서 임시 토픽의 QoS 1 발행·구독 왕복을 통합 테스트한다.

## 검증 명령

```bash
.venv/bin/python -m pytest -q
SMART_DESK_RUN_MQTT_INTEGRATION=1 \
  .venv/bin/python -m pytest -q -m mqtt_integration
```

## 완료 조건

연결 단절 후 같은 프로세스가 자동으로 재연결·재구독하며, MQTT 모듈 밖의
애플리케이션 기능 코드가 aiomqtt 또는 Paho 객체를 직접 참조하지 않는다. 가짜
연결 단절과 실제 EMQX에 연결된 client 강제 단절 양쪽에서 재연결 경로를 검증한다.
