# 03. 책상 제어

> 현재 상태: Python·fake 구현, FIN firmware 검증과 relay 분리 board bench 완료.
> production firmware upload 완료. MQTT session 검증과 실제 책상 이동은 미완료다.

## 목표

`DeskController` 한 곳에서 목표 이동, 수동 HOLD, STOP과 센서·MQTT 안전 중단을
관리하고, 기존 배선에 맞춘 FIN 전용 ESP32-C3 MQTT relay firmware가 독립 안전
경계에서 명령을 실행한다.

## 선행 조건

- [책상 I/O 어댑터](02-desk-io.md) 완료
- [책상 제어와 안전](../architecture/desk-safety.md)의 미확정 펄스·timeout 값 확인
- `.scratch/designs/03-desk-control-overview.md`와 상세 설계
  `.scratch/designs/03-desk-control-design.md`, 필수 하위 설계
  `.scratch/designs/03-01-esp32-relay-firmware-design.md` 검토

## 작업 목록

- [x] Task 02의 `Direction`을 재사용하고 `DeskState`, `DeskSnapshot`을 불변 모델로
  정의한다.
- [x] lifecycle `start()`·`stop()`과 명령 `stop_motion(reason)`을 분리한다.
- [x] `set_target()`, 목표 증감, `hold_up()`, `hold_down()`을 구현한다.
- [x] 목표·수동 제어 범위 75~115cm와 센서 신선도를 모든 이동 전에 검증한다.
- [x] 수동 HOLD watchdog, 목표 timeout과 STOP 우선순위를 구현한다.
- [x] 같은 방향 pulse를 만료 전에 갱신해 ESP32가 종료 시각만 연장하도록 하고,
  갱신 주기가 끊기면 pulse timeout으로 정지하게 한다.
- [ ] 기존 MQTT RTT 측정의 평균뿐 아니라 tail latency를 고려하고, 실제 ESP32
  수신 시각 기준 갱신 간격을 측정해 `hold_ms`와 반복 주기를 확정한다.
- [x] ESP32 릴레이 펌웨어가 `WiFi.setSleep(false)`를 유지하는지 source와 build에서 확인하고,
  Wi-Fi Modem-sleep 상태에서는 실물 연속 제어를 시작하지 않는다.
- [x] 명령 lock 안에서 MQTT I/O를 기다리지 않도록 상태전이를 분리한다.
- [x] task 취소, 예외, 애플리케이션 종료 경로에서 STOP을 보장한다.
- [x] `AppContainer.desk`, `get_desk()`와 lifecycle 순서를 연결한다.
- [x] 기존 GPIO 3 UP·GPIO 4 DOWN 배선을 유지하는 FIN 전용 ESP32-C3 MQTT relay
  firmware를 새로 작성한다.
- [x] ESP32가 retained height 하나로 제어를 시작하지 않도록 MQTT session마다
  distinct live height로 재무장하고 bounded height lease를 적용한다.
- [x] ESP32가 network loop와 독립된 hardware timer ISR로 모든 UP/DOWN을 최대 500ms 안에
  OFF하고, 같은 방향에서는 GPIO 재접점 없이 deadline만 연장하도록 한다.
- [x] firmware에서 73~118cm 측정과 75~115cm 방향별 제어 차단, Wi-Fi·MQTT·height
  단절 STOP과 explicit STOP live 응답을 구현한다.

## 테스트

- [x] 목표 도달, 새 목표, 상·하한, 센서 만료와 MQTT 발행 실패 상태전이를 검증한다.
- [x] HOLD 갱신 중 이동과 갱신 중단 후 watchdog STOP을 검증한다.
- [x] 같은 방향 갱신 간격이 `hold_ms`보다 짧고 연속 명령 사이에 불필요한 STOP 호출이
  생기지 않는 계약을 가짜 시각과 RelayClient 호출 기록으로 검증한다.
- [x] 목표 전환과 STOP 경합, 진행 중 pulse와 STOP 경합에서 STOP 우선순위를 검증한다.
- [x] 실제 장치 전에는 가짜 `RelayClient`로 발행 펄스 수와 최종 STOP을 확인한다.
- [x] firmware native policy test와 ESP32-C3 clean build를 검증한다.
- [x] relay를 책상에서 분리한 상태에서 부팅 OFF, GPIO 상호 배제, 50/500ms timer,
  같은 방향 연장, 방향 전환, 명시적 STOP과 1초 main-loop stall 중 OFF를 검증한다.
- [ ] logic analyzer로 30ms both-OFF를 실측하고 MQTT session arming, height lease와
  Wi-Fi·broker disconnect STOP을 검증한다.
- [x] Python `RelayStatusMessage`와 firmware의 `online`, `heartbeat`, `moving`,
  `stopped`, `rejected`, `offline` payload 호환을 검증한다.

자동 검증 완료 증거:

- Python 전체 `pytest`와 `compileall` 통과
- PlatformIO native policy/protocol 6개 test 통과
- ESP32-C3 clean build 통과
- ELF에서 timer wrapper와 callback의 `.iram0.text` 배치 확인
- relay 분리 serial bench에서 UP/DOWN 50ms가 각각 장치 시각 51ms 뒤 OFF
- 같은 방향 500ms 연장이 마지막 갱신 501ms 뒤 OFF, 모든 snapshot에서 GPIO 상호 배제
- main loop 1초 정지 중 hardware timer가 GPIO를 독립 OFF하는 것을 확인
- FIN production firmware `smartdesk-fin-relay-1.0.0` upload와 verify 완료

미완료 검증:

- logic analyzer를 사용한 실제 GPIO edge와 30ms both-OFF 실측
- MQTT session arming, height lease와 Wi-Fi/MQTT 단절 board test
- safe MQTT integration과 실제 책상 이동

현재 AP 연결은 ESP32 disconnect reason 15(WPA 4-way handshake timeout)에서
중단된다. production firmware는 fail-closed STOP 상태이며 MQTT 이동 명령을 받지 않는다.

## 완료 조건

Python 단위·통합 테스트와 FIN firmware build·비이동 검증이 통과하고, 승인된
firmware를 올린 제한된 실물 범위에서 UP, DOWN, STOP과 목표 도달을 확인한다.
센서, height lease, Wi-Fi 또는 MQTT가 끊기면 firmware deadline 안에 책상이
정지해야 한다.
