# 책상 제어 방식과 작업 모드

`AutomationService`는 등록·익명 사용자 session의 제어 방식, active 작업 모드, 자세 안정화,
park와 상위 사용자 의도를 소유한다. `DeskController`는 센서·relay 안전, 실제 목표 교체와
STOP을 계속 소유한다.

## 서로 다른 두 mode

| API 이름 | 화면 이름 | 의미 |
| --- | --- | --- |
| `controlMode` | 제어 방식 | `AUTO` 또는 `MANUAL`: 책상을 누가·어떻게 움직이는가 |
| `activityMode` | 작업 모드 | 기본·독서·공부 등: 어떤 앉기/서기 높이와 LED 묶음을 쓰는가 |

두 값은 독립적이다. 예를 들어 `AUTO + 독서`는 자세에 따라 독서용 높이를 자동 적용하고,
`MANUAL + 독서`는 독서 설정을 active로 유지하되 자세 변화로 책상을 움직이지 않는다.

## 제어 방식

| 값 | 의미 |
| --- | --- |
| `AUTO` | fresh하고 안정된 자세에 따라 현재 작업 모드의 높이를 선택 |
| `MANUAL` | Vision은 관측만 하고 사용자 명령이 책상을 제어 |

session이 없으면 `controlMode`와 `activityMode`도 없다. HOLD, 직접 목표와 STOP 같은 신원
독립 명령은 session을 만들지 않고 실행할 수 있다.

```text
session 없음
  ├─ 단일 재실·자세 3초 → 등록/익명 session + AUTO
  └─ HOLD / 직접 목표 / STOP → session·mode 없이 명시적 수동 실행

AUTO
  ├─ 직접 목표 / HOLD / 사용자 STOP → MANUAL, activityMode 유지
  ├─ activityMode 변경 → AUTO 유지, fresh 자세로 안전한 목표 재평가
  ├─ Vision 불확실 → AUTO 유지 + BLOCKED
  └─ 안정 VACANT → AUTO STOP + session·mode 종료

MANUAL
  ├─ activityMode 변경 → MANUAL 유지, LED만 적용하고 책상은 이동하지 않음
  ├─ 사용자가 AUTO 재활성화 → STOP → 자세 후보 초기화 → AUTO
  └─ 안정 VACANT → session·mode 종료
```

- 시간 경과만으로 `MANUAL → AUTO` 전환하지 않는다.
- 수동 명령이 장치 상태 때문에 실패해도 활성 session의 `MANUAL`을 유지한다.
- 사용자 STOP은 활성 session을 MANUAL로 만들고, Vision·장치 안전 STOP은 control mode를
  보존한다.
- 같은 session에서 AUTO를 다시 선택하면 이전 자세 snapshot으로 움직이지 않고 fresh 자세를
  5초 다시 확인한다.
- 여러 Dashboard가 열려도 서버 snapshot이 기준이다.

## 작업 모드 모델

등록 profile의 기존 앉기·서기 높이와 LED 색상은 삭제 불가능한 `기본` 작업 모드다. 사용자
정의 작업 모드는 같은 세 값을 `profile_modes`에 저장한다. 단일 높이 custom preset은 두지
않고, 일회성 위치는 직접 목표로 처리한다.

| 필드 | 규칙 |
| --- | --- |
| `key` | 기본은 `default`, custom은 서버 생성 ID |
| `kind` | `DEFAULT` 또는 `CUSTOM` |
| `name` | 기본은 고정 `기본`, custom은 profile 안에서 정규화 중복 금지 |
| `sittingHeightCm` | 75~115cm |
| `standingHeightCm` | 75~115cm |
| `ledColor` | `RRGGBB` 또는 `null`; `null`은 적용 시 LED OFF |
| `editable` | 기본 `false`, custom `true` |

설정 화면 조회는 profile 기본값과 custom row를 `EffectiveActivityMode[]`로 합성한다. 활성
session은 선택 시점의 유효 값을 snapshot으로 보관한다. 설정 화면에서 저장값을 수정해도
현재 책상이나 LED가 즉시 바뀌지 않고, 다음 모드 선택 또는 다음 session부터 반영된다.

현재 active custom mode 삭제는 `409`다. 기본 모드는 삭제할 수 없다.

## session 시작과 종료의 작업 모드·LED

### 등록 session

1. 새 session은 `controlMode=AUTO`로 시작한다.
2. profile의 `기본` 작업 모드를 active snapshot으로 선택한다.
3. 기본 모드의 LED 색상을 한 번 적용한다. 색상이 `null`이면 OFF를 요청한다.
4. 최초 AUTO 책상 목표는 기존 규칙대로 session 생성 후 조건을 2초 더 확인한다.

### 익명 session

익명 session은 profile이나 custom 작업 모드를 만들지 않는다. 높이 정책은 앉음 75cm·섬
110cm인 `ANONYMOUS_DEFAULT`이고 `activityMode=null`이다. 등록 session 종료 시 LED를 OFF한
상태를 유지하며 익명 session 시작은 새 색상을 적용하지 않는다.

### session 종료·교대

session 종료 시 active mode snapshot과 session LED override를 폐기하고 WLED OFF를
best-effort로 요청한다. 사용자 교대는 이전 session을 끝내고 OFF 처리한 뒤 새 등록 session의
기본 색상을 적용한다. WLED 실패는 session 종료·교대와 Desk STOP을 rollback하지 않고 WLED
상태만 `DEGRADED`/`ERROR`로 남긴다.

## 작업 모드 전환

Dashboard와 Agents SDK tool은 `activityModeKey`와 `expectedSessionId`만 보낸다. 서버가 현재
profile 소유권과 저장값을 다시 조회한다.

```text
작업 모드 선택
  → expectedSessionId와 current session 비교
  → 등록 profile의 default/custom 소유권과 값 조회
  → AutomationService command lock
      → active mode snapshot 교체
      → session LED override 제거
      → 새 mode LED 적용 시도
      → 기존 controlMode에 따라 책상 처리
```

### AUTO에서 전환

- `controlMode=AUTO`를 유지한다.
- 이전 자동 generation과 자세 후보를 무효화하고, 진행 AUTO 목표가 다르면 STOP 후 교체한다.
- 현재 자세가 fresh하고 이미 안정적으로 사용 가능하면 새 모드의 해당 자세 높이를 안전하게
  재평가한다.
- 자세·Vision이 불확실하면 active mode와 LED는 바꾸되 책상 이동은 `BLOCKED`로 유지한다.
- 장치가 준비되지 않아도 mode 선택 자체는 유지하며 이동만 차단한다.

### MANUAL에서 전환

- `controlMode=MANUAL`을 유지한다.
- active mode와 LED만 바꾸고 책상은 움직이지 않는다.
- 이후 사용자가 AUTO를 명시적으로 선택하면 그 active mode를 사용해 fresh 자세를 5초 확인한다.

## 수동 LED 변경

Dashboard 또는 Agent의 수동 WLED 색상 변경은 현재 session의 임시 override다. 저장된 작업
모드를 수정하지 않는다. 다음 작업 모드 전환 또는 다음 session 시작은 override를 지우고
저장된 색상 또는 OFF를 다시 적용한다. WLED 실패는 작업 모드 선택과 Desk 정책을 되돌리지
않는다.

## 높이 정책과 AUTO

| session | active 높이 정책 | 작업 모드 |
| --- | --- | --- |
| 등록 사용자 | active mode의 앉기·서기 높이 | 기본 또는 해당 profile custom |
| 익명 사용자 | 75cm / 110cm | 없음 |
| session 없음 | 없음 | 없음 |

상단 몸체 또는 얼굴 한 명, 하단 하체 한 명과 자세가 3초 안정화돼 session이 생긴 뒤 2초를
더 기다린다. 동일 session, 단일 재실, 같은 자세, fresh frame과 장치 준비가 유지된 경우에만
첫 목표를 만든다.

최초 목표 이후 자세 전환과 명시적 AUTO 재활성화는 동일 조건의 fresh 자세를 5초 확인한다.
현재 높이가 목표 허용 오차 안이면 이동을 만들지 않고, 같은 자세·같은 목표를 frame마다
반복 설정하지 않는다.

자동화 상태는 control mode와 별도로 `WAITING_USER`, `OBSERVING`, `READY`, `MOVING`,
`MANUAL`, `BLOCKED`, `PARK_WAITING`, `PARKING`을 제공한다. `AUTO`이면서 Vision 만료로
`BLOCKED`일 수 있다.

## 사용자 전환 중 목표 교체

익명 AUTO 중 등록 얼굴 A가 확인되면 새 session ID를 발급하고 A의 기본 작업 모드를
활성화한다. WLED에는 A의 기본 색상을 적용하고, 현재 fresh 자세의 기본 모드 높이로 목표를
안전하게 교체한다.

익명 session이 MANUAL이었다면 새 등록 session도 MANUAL을 보존하고 A의 기본 작업 모드와
LED만 적용한다. A→B 또는 고품질 미등록 얼굴 전환처럼 실제 사용자가 달라진 경우에는 기존
AUTO를 STOP하고 새 session 정책을 시작한다.

## Vision 차단과 장치 차단

Vision의 다중 사용자, count·timestamp 불일치, 자세 귀속 불가와 stale 결과는 AUTO와 PARK만
STOP·차단한다. HOLD, 직접 목표와 STOP은 허용한다.

height stale·invalid, MQTT/relay 미준비, ACK 오류, 범위 밖 목표와 ESP32 안전 상태는 AUTO,
PARK와 수동 이동을 모두 차단한다. STOP은 항상 접수한다. 현재 운영 relay transport는
Wi-Fi/MQTT이며 serial bridge fallback은 없다.

## 직접 수동 명령

HOLD와 직접 목표는 활성 session이 있으면 먼저 `controlMode=MANUAL`로 바꾸고 자동
generation을 무효화한다. active activity mode는 바꾸지 않는다. 사용자 STOP도 같은 session의
control mode만 MANUAL로 만들며 active mode는 유지한다.

session이 없어도 HOLD, 직접 목표와 STOP을 허용한다. STOP과 목표 CANCEL은 session 검증보다
먼저 처리한다. 자동화와 Dashboard는 relay를 직접 호출하지 않고 `DeskController` 공개
메서드만 사용한다.

Agents SDK Desk function tool은 Dashboard와 같은 `AutomationService` public command를
호출한다. 실제 부작용 실행 직전에 turn 시작 `sessionId`를 command lock 안에서 다시 비교한다.

## 빈자리 park

안정 `VACANT`로 session이 끝나면 먼저 active mode를 제거하고 WLED OFF를 요청한 뒤
`PARK_WAITING`을 시작한다. 두 카메라의 fresh VACANT가 30초 계속되고 활성 수동 의도·새
session이 없으며 장치가 준비된 경우에만 75cm PARK 목표를 만든다.

사람 후보, 새 session 후보, 수동 명령, camera·height·MQTT·relay 오류와 서버 종료는 대기나
진행 park를 취소하고 PARK 이동을 STOP한다. 서버 시작 직후 과거 VACANT나 retained height로
움직이지 않는다.
