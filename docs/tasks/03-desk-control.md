# 03. 책상 제어

## 목표

`DeskController` 한 곳에서 목표 이동, 수동 HOLD, STOP과 센서·MQTT 안전 중단을
관리한다.

## 선행 조건

- [책상 I/O 어댑터](02-desk-io.md) 완료
- [책상 제어와 안전](../architecture/desk-safety.md)의 미확정 펄스·timeout 값 확인

## 작업 목록

- [ ] `DeskState`, `Direction`, `DeskSnapshot`을 불변 모델로 정의한다.
- [ ] lifecycle `start()`·`stop()`과 명령 `stop_motion(reason)`을 분리한다.
- [ ] `set_target()`, 목표 증감, `hold_up()`, `hold_down()`을 구현한다.
- [ ] 목표 범위 75~118cm와 센서 신선도를 모든 이동 전에 검증한다.
- [ ] 수동 HOLD watchdog, 목표 timeout과 STOP 우선순위를 구현한다.
- [ ] 명령 lock 안에서 MQTT I/O를 기다리지 않도록 상태전이를 분리한다.
- [ ] task 취소, 예외, 애플리케이션 종료의 `finally`에서 STOP을 보장한다.
- [ ] `AppContainer.desk`, `get_desk()`와 lifecycle 순서를 연결한다.

## 테스트

- [ ] 목표 도달, 새 목표, 상·하한, 센서 만료와 MQTT 단절 상태전이를 검증한다.
- [ ] HOLD 갱신 중 이동과 갱신 중단 후 watchdog STOP을 검증한다.
- [ ] 동시 TARGET·HOLD·STOP에서 STOP이 우선하는지 검증한다.
- [ ] 실제 장치 전에는 가짜 `RelayClient`로 발행 펄스 수와 최종 STOP을 확인한다.

## 완료 조건

모든 단위·통합 테스트가 통과하고, 제한된 실물 범위에서 UP, DOWN, STOP과 목표
도달을 확인한다. 센서 또는 MQTT를 끊었을 때 책상이 즉시 정지해야 한다.
