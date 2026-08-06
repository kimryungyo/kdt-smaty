# 02. 책상 I/O 어댑터

## 목표

Arduino 높이 입력과 ESP32 릴레이 MQTT 프로토콜을 정책 계층에서 분리한다. 이
단계에서는 목표 높이 제어 루프를 만들거나 실제 책상을 움직이지 않는다.

## 선행 조건

- [MQTT 기반](01-mqtt-foundation.md) 완료
- 기존 `/srv/smart-desk/docs/MQTT_PROTOCOL.md`와 ESP32 펌웨어 계약 재확인

## 작업 목록

- [x] `SerialSettings`와 `SerialLineSource`를 구현한다.
- [x] 포트 열기 실패, read timeout, 종료와 재연결 상태를 snapshot으로 제공한다.
- [x] `SegmentDecoder`를 순수 변환기로 이식하고 73~118cm 측정 범위를 검증한다.
- [x] `HeightSnapshot`과 `DeskHeightMonitor`를 구현한다.
- [x] 오래된 높이를 `STALE`로 판정하고 retained MQTT 높이를 새 측정으로 오인하지 않는다.
- [x] ESP32 명령·상태 Pydantic 모델과 기존 토픽을 이식한다.
- [x] `RelayClient.handle_status()`, `pulse()`, `send_stop()`,
  `get_snapshot()`을 구현한다.
- [x] 센서·물리 범위 73~118cm와 제어 범위 75~115cm의 계약 차이를 정리한다.

## 테스트

- [x] 잘린 라인, 잘못된 JSON, 범위 밖 높이와 정상 높이를 검증한다.
- [x] 센서 신선도와 `STALE` 전환을 가짜 시각으로 검증한다.
- [x] 릴레이 명령이 QoS 1, `retain=false`이고 hold 범위를 지키는지 검증한다.
- [x] `send_stop()`이 올바른 STOP 메시지를 발행하는지 가짜 MQTT로 확인한다.

## 완료 조건

가짜 시리얼과 가짜 MQTT만으로 높이 snapshot과 ESP32 상태 snapshot을 재현할 수
있고, 실제 릴레이 이동 없이 STOP 메시지 계약까지 검증된다.

## 구현 결과

- Arduino가 없어도 MQTT 시작 뒤 애플리케이션은 `READY`가 되고 높이 상태만
  `ERROR`가 되는 것을 통합 테스트로 확인했다.
- 높이 관측은 QoS 1 retained, 릴레이 명령은 QoS 1 non-retained로 고정했다.
- 실제 운영 토픽 발행, 펌웨어 변경과 책상 이동은 수행하지 않았다.
