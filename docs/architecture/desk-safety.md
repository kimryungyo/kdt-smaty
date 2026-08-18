# 책상 제어와 안전

책상 제어는 단일 프로세스 구조에서도 독립 안전 계층을 유지해야 한다. 이 문서는
새 `DeskController`의 책임 경계를 정의하며, 실제 수치와 메시지 계약은 구현 전
`/srv/smart-desk/docs/SAFETY.md`, `MQTT_PROTOCOL.md`, ESP32 펌웨어와 대조한다.

## 제어 경계

```text
FastAPI / Automation
        │ set_target, hold, stop_motion
        ▼
DeskController
  ├─ 목표 범위·센서 신선도·상태전이 검증
  └─ RelayClient
        │ MQTT pulse / STOP
        ▼
ESP32 firmware
  ├─ Wi-Fi·MQTT·height lease 단절 정지
  ├─ network loop와 독립된 펄스 최대 시간
  └─ 75~115cm 방향별 독립 차단
```

상위 계층의 검증은 편의와 정책을 위한 것이며, ESP32 보호를 대체하지 않는다.

## 높이 기준

| 구분 | 범위 또는 상한 | 의미 |
| --- | ---: | --- |
| 세그먼트 측정 범위 | 73~118cm | 높이 표시기에서 유효한 값으로 해석할 수 있는 측정 범위 |
| 책상 물리 범위 | 73~118cm | 실제 책상과 센서가 관측할 수 있는 고정 범위 |
| 사용자 제어 범위 | 75~115cm | 자동 목표와 수동 조절에 허용하는 기본 범위 |

세그먼트 측정 범위와 책상 물리 범위는 73~118cm이고, 실제 이동 명령은 양 끝에
여유를 둔 75~115cm 안에서만 허용한다. Python의 고정 기준은
`src/smart_desk/config/constants.py`의 물리 최소·최대 상수와 제어 최소·최대
상수다. 환경 설정은 이 범위를 더 좁힐 수 있지만 넓힐 수 없다. ESP32 펌웨어에도
75~115cm 제어 차단 기준을 적용해야 한다.

## 세그먼트 표시기 자동 절전

물리 모션데스크 표시기는 마지막 제어 이후 약 30초가 지나면 자동 절전으로 꺼진다.
이때 Arduino와 USB serial 연결은 유지되지만 자리 선택 신호의 정상적인 multiplexing이
중단되어 세 자리를 모두 새로 확정한 `fresh=7` 높이를 만들 수 없다. 현재 서버는
`fresh=7`만 실측 높이로 인정하므로 기존 값은 기본 1초 뒤 `STALE`이 되고, 서버가
절전 중 시작되면 높이가 `WAITING`에 머문다.

`STALE` 또는 `SENSOR_SLEEPING` 높이에서 일반 이동은 의도적으로 잠긴다. 다만 마지막
유효 측정이 측정 범위 안에 남아 있으면 `DeskController.set_target()`만이 그 값을 방향·경계
확인용 근거로 사용해 제한된 `WAKE`를 보낼 수 있다. WAKE 뒤에는 새 live 측정과 ESP32
height lease가 확인되기 전까지 UP/DOWN 이동을 시작하지 않는다. `WAITING`, `ERROR`,
범위 밖 또는 시각 없는 값에는 WAKE도 허용하지 않는다.

상위 정책(AUTO, PARK, Dashboard)은 높이의 `ONLINE` 여부를 별도로 판정하지 않고 모두
`DeskController.set_target()`을 호출한다. 따라서 어떤 목표 출처든 같은 WAKE·fresh-height
안전 계약을 사용한다. cached 값을 새 관측처럼 `observed_at`만 갱신해서는 안 된다.
그렇게 하면 이동 중 실제 높이 단절을 감지하는 안전 규칙을 우회하게 된다.

## DeskController 상태

| 상태 | 의미 |
| --- | --- |
| `IDLE` | 목표와 수동 명령 없이 정지 |
| `MOVING` | 유효한 센서 높이를 기준으로 목표로 이동 |
| `MANUAL` | 사용자가 HOLD를 유지하는 동안 이동 |
| `STOPPED` | 명시적 정지 또는 안전 중단 후 정지 |
| `ERROR` | 센서·장치·제어 오류로 자동 이동 차단 |

필요하면 기존 동작 호환을 위해 `WAKING`, `REACHED` 같은 세부 상태를 추가할 수
있다. 상태 이름보다 STOP 조건과 각 상태의 허용 명령을 테스트로 고정하는 것이
중요하다.

## 필수 규칙

- 유효하고 충분히 최근인 높이 측정 없이는 자동 목표 이동을 시작하거나 계속하지 않는다.
  단, `STALE`/`SENSOR_SLEEPING`의 마지막 유효 측정은 fresh 측정을 얻기 위한 제한된
  WAKE의 방향·경계 근거로만 쓸 수 있다.
- 75~115cm 제어 범위를 벗어난 목표는 거부한다. 임의로 최대·최소값으로 보정하지 않는다.
- 현재 높이가 115cm 이상이면 UP 펄스를 발행하지 않고 즉시 STOP한다.
- 수동 HOLD는 watchdog 안에 갱신되지 않으면 STOP한다.
- 새 목표, 재실/자세 불확실, Vision 신선도 만료, MQTT 오류, 센서 오류는 진행 중
  이동을 STOP한다.
- 서버 종료, task 취소, 예외 처리 경로에서 `RelayClient.send_stop()`을 호출한다.
- `RelayClient.pulse()`는 `DeskController`만 호출한다.

## 연속 이동 pulse

ESP32는 같은 방향 pulse를 현재 `hold_ms` 만료 전에 다시 받으면 릴레이를
OFF→ON하지 않고 자동 종료 시각만 연장한다. 이 동작으로 연속 이동 중 기계식
릴레이의 불필요한 딸깍임을 줄인다.

`DeskController`의 갱신 주기는 항상 `hold_ms`보다 짧고 네트워크 지연을 흡수할
여유가 있어야 한다. 반대로 갱신 요청이 끊기면 ESP32의 기존 만료 시각에 릴레이가
꺼져야 하므로 무기한 ON 명령은 사용하지 않는다. 정확한 `hold_ms`와 갱신 간격은
[통합·실물 검증](../tasks/09-system-validation.md)에서 다시 확인한다.

현재 비이동 구현 후보는 continuous/manual 500ms, fine 100ms, refresh 100ms와 poll
50ms다. 이는 실물 확정값이 아니며 ESP32 callback 수신 간격과 GPIO edge 측정 전에는
실제 연속 제어 근거로 사용하지 않는다.

FIN 전용 ESP32 펌웨어는 main MQTT loop가 socket·reconnect 작업에서 지연돼도
`hold_ms <= 500`을 넘겨 relay가 켜지지 않도록 network 처리와 독립된 one-shot
timer를 사용한다. 같은 방향 명령은 GPIO를 다시 쓰지 않고 timer만 재설정하며,
STOP과 반대 방향에서 두 출력을 먼저 OFF한다.

현재 고정된 Arduino core에서는 legacy hardware timer interrupt를
`ESP_INTR_FLAG_IRAM`으로 등록하고 ISR이 ESP32-C3 GPIO clear register를 직접 기록한다.
ELF의 ISR 배치는 확인했지만 실제 50/500ms OFF edge와 network-loop stall 동작은 relay
분리 board test로 추가 확인해야 한다.

ESP32-C3의 기본 Wi-Fi Modem-sleep은 MQTT 수신 지연을 크게 늘릴 수 있으므로 릴레이
제어 펌웨어는 기존처럼 `WiFi.setSleep(false)`를 유지한다. 2026-08-06 실측에서
안정된 절전 OFF 반복의 MQTT RTT는 평균 약 43~44ms, p95 약 86~88ms였다. 그러나
같은 조건의 다른 60초 측정에서 평균 100.723ms, p95 254.680ms의 일시적 고지연도
관찰됐으므로 100ms를 지연 상한으로 간주하지 않는다. 이 수치는 왕복 시간이며
단방향 도달 시간도 아니다. 평균값만으로 갱신 주기를 정하지 않고 실제 ESP32 수신 시각
기준의 같은 방향 명령 도착 간격과 무중단 연장을 별도로 검증한다.

## ESP32 height freshness

높이 topic은 retained이므로 MQTT reconnect 직후 받은 첫 높이는 현재 센서 관측이라고
단정할 수 없다. FIN 펌웨어는 MQTT session마다 높이 상태를 제거하고, 서로 다른
`observed_at`을 가진 live height를 추가로 받은 뒤에만 이동을 무장한다. 이후에도
ESP32 receipt 시각 기준의 bounded height lease 안에서 distinct 관측이 계속돼야 한다.

- retained 높이 하나만으로 UP/DOWN을 허용하지 않는다.
- 같은 `observed_at`의 QoS duplicate는 lease를 갱신하지 않는다.
- height가 invalid 또는 stale이면 진행 중 relay를 즉시 OFF한다.
- fresh height가 115cm 이상일 때 UP, 75cm 이하일 때 DOWN이면 다음 pulse를 기다리지
  않고 firmware가 독립적으로 OFF한다.
- Wi-Fi·MQTT reconnect마다 height를 다시 무장한다.

세부 계약은 `.scratch/designs/03-01-esp32-relay-firmware-design.md`를 따른다.

## 대시보드 수동 조절

```text
버튼 누름 → hold_up() 또는 hold_down()
누르는 동안 → 같은 HOLD 요청을 주기적으로 반복
버튼 놓음 → stop_motion("사용자 요청")
요청 단절 → DeskController watchdog → STOP
```

HTTP 요청 하나를 버튼을 누르는 전체 시간 동안 열어두지 않는다. 브라우저가 짧은
HOLD 요청을 반복하고, 서버는 마지막 요청 시각을 기준으로 입력 단절을 판단한다.
별도 수동 제어 session 객체는 만들지 않는다.

## 동시성 규칙

목표 변경과 수동 제어가 동시에 들어올 수 있으므로 `DeskController`는 command
lock을 소유한다. lock 안에서는 상태와 목표만 갱신하고, 실제 MQTT 전송은 필요한
경우 짧은 후속 작업으로 처리한다. `STOP`은 대기 중인 다른 명령보다 먼저 반영할
수 있도록 취소 신호 또는 세대 번호를 사용한다.

## 구현 전 확인 목록

- 세그먼트·물리 범위 73~118cm와 제어 범위 75~115cm 분리 여부
- 일반·미세 펄스 시간, 갱신 주기와 펌웨어 최대 펄스
- 목표 도달 허용 오차, 관성 보정, 수동 watchdog
- ESP32 height session arming·lease와 연결 끊김 동작
- ESP32 one-shot timer, 부팅 GPIO OFF와 explicit STOP live 응답
- 기존 `/desk_ctl`, `/desk_ctl_status` 메시지 계약

이 값을 새 문서에서 추정해 바꾸지 않는다. 확정 뒤에는 설정 한 곳과 ESP32
펌웨어에 같은 보호 원칙을 반영하고, 계약·상태전이 테스트로 검증한다.
