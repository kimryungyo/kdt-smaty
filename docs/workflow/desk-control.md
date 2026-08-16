# 책상 제어 모드와 높이 프리셋

`AutomationService`는 등록·익명 사용자 session의 `AUTO`/`MANUAL`, 자세 안정화, park와
상위 사용자 의도를 소유한다. `DeskController`는 센서·relay 안전, 실제 목표 교체와 STOP을
계속 소유한다.

## 제어 모드

| 모드 | 의미 |
| --- | --- |
| `AUTO` | 안정화된 자세에 따라 session 높이 정책을 선택 |
| `MANUAL` | Vision은 관측만 하고 사용자 명령이 책상을 제어 |

session이 없으면 runtime mode도 없다. 단, HOLD, 직접 목표와 STOP 같은 신원 독립 수동
명령은 session을 만들지 않고 실행할 수 있다.

```text
session 없음
  ├─ 단일 재실·자세 3초 → 등록 또는 익명 session + AUTO
  └─ HOLD / 직접 목표 / STOP → session·mode 없이 명시적 수동 실행

AUTO
  ├─ preset / 직접 목표 / HOLD / 사용자 STOP → MANUAL
  ├─ Vision 불확실 → AUTO mode 유지 + BLOCKED
  └─ 안정 VACANT → AUTO STOP + session·mode 종료

MANUAL
  ├─ 명시적 AUTO → STOP → 자세 후보 초기화 → AUTO
  └─ 안정 VACANT → session·mode 종료
```

- 시간 경과만으로 `MANUAL → AUTO` 전환하지 않는다.
- 수동 명령이 장치 상태 때문에 실패해도 활성 session의 `MANUAL`을 유지한다.
- 사용자 STOP은 활성 session을 MANUAL로 만들고, Vision·장치 안전 STOP은 mode를 보존한다.
- AUTO 전환 직후 이전 자세 snapshot으로 움직이지 않고 자세를 다시 안정화한다.
- 여러 Dashboard가 열려도 서버 mode와 session snapshot이 기준이다.

## session별 높이 정책

| session | 앉은 높이 | 선 높이 | custom preset |
| --- | ---: | ---: | --- |
| 등록 사용자 | `Profile.sittingHeightCm` | `Profile.standingHeightCm` | 해당 profile 소유 row |
| 익명 사용자 | 기본 75cm | 기본 110cm | 없음 |
| session 없음 | 없음 | 없음 | 없음 |

익명 기본 높이와 빈자리 park 높이는 profile row가 아니라 자동화 설정에 둔다. 현재 제어 하한과
익명 앉은 높이가 모두 75cm여도 같은 상수를 의미상 재사용하지 않는다.

## 최초 AUTO 이동

상단 몸체 또는 얼굴 한 명, 하단 하체 한 명과 자세가 3초 동안 안정화돼 session이 생긴 뒤
2초를 더 기다린다. 대기 동안 동일 session, 단일 재실, 같은 자세, fresh frame과 장치
준비가 모두 유지된 경우에만 첫 목표를 만든다.

```text
익명 + SITTING  → 2초 뒤 75cm
익명 + STANDING → 2초 뒤 110cm
등록 A + 자세    → 2초 뒤 A profile 높이
```

대기 중 자세·session·재실 변경, 수동 명령, frame 만료와 장치 오류는 첫 목표를 취소한다.

## AUTO 자세 제어

```text
VisionSnapshot
  + CurrentUserSnapshot
  + session 높이 정책
  + DeskSnapshot
  → AutomationService
      → freshness·단일 사용자·귀속 검사
      → 자세 유지 시간 검사
      → 같은 목표 반복 억제
      → DeskController.set_target()
```

| 안정된 관측 | 동작 |
| --- | --- |
| 등록 session + `SITTING`/`STANDING` | profile 자세 높이 |
| 익명 session + `SITTING` | 75cm |
| 익명 session + `STANDING` | 110cm |
| 자세 `UNKNOWN` 또는 관련 frame stale | AUTO STOP·BLOCKED, session 유지 |
| `MULTIPLE` 또는 count 불일치 | AUTO STOP·BLOCKED, session 유지 |
| 안정 `VACANT` | AUTO STOP, session 종료와 park 대기 |

동일 session과 자세가 유지 시간 동안 이어진 뒤 목표를 한 번 설정한다. 자세, mode, session,
재실 결합과 frame freshness 변경은 후보와 timer를 초기화한다.

등록 session에서 얼굴만 보이지 않지만 fresh 단일 재실이 계속되면 AUTO를 유지한다.
`MULTIPLE`이나 count 불일치 후에는 같은 등록 얼굴을 재확인해야 AUTO를 재개한다. 익명
session은 fresh 단일 재실을 3초 재안정화하면 재개한다.

자동화 상태는 mode와 별도로 `WAITING_USER`, `OBSERVING`, `READY`, `MOVING`, `MANUAL`,
`BLOCKED`, `PARK_WAITING`, `PARKING`을 제공한다. `BLOCKED`의 Vision 원인은 AUTO intent만,
장치 원인은 모든 이동을 차단한다.

## 사용자 전환 중 목표 교체

익명 AUTO 중 등록 얼굴 A가 확인되면 새 session ID를 발급하고 현재 fresh 자세의 profile
목표로 즉시 바꾼다. `DeskController.set_target()`은 기존 목표가 다르면 기존 이동 의도를
무효화하고 live STOP을 확인한 뒤 현재 높이에서 새 방향과 목표를 계산한다.

익명 session이 MANUAL이었다면 등록 identity 확정만으로 AUTO로 바꾸지 않는다. A→B 또는
고품질 미등록 얼굴 전환처럼 실제 사용자가 달라진 경우에는 기존 AUTO를 STOP하고 새 AUTO
session과 자세 안정화를 시작한다.

## Vision 차단과 장치 차단

### Vision 차단

- 다중 사용자
- 카메라 count·timestamp 불일치
- 자세 `UNKNOWN` 또는 사람 귀속 불가
- 관련 frame·detector 결과 만료

진행 AUTO와 PARK를 STOP하고 새 자동 목표를 금지한다. HOLD, 직접 목표와 STOP은 허용한다.

### 장치 차단

- height stale·invalid
- MQTT/relay 미준비 또는 ACK 오류
- 제어 범위 밖 목표
- 상·하한에서 금지 방향
- ESP32 안전 상태

AUTO, PARK와 수동 이동을 모두 차단한다. STOP은 항상 접수한다.

## 수동 명령

HOLD, 직접 목표와 preset은 활성 session이 있으면 먼저 `MANUAL`로 전환하고 AUTO generation을
무효화한다. 자동화와 Dashboard는 relay를 직접 호출하지 않고 `DeskController` 공개
메서드만 사용한다.

HOLD는 현재처럼 주기적으로 갱신하고 pointer/keyboard release, blur, page hide와 unmount에서
STOP한다. 요청 유실 시 `DeskController` watchdog과 ESP32 pulse timeout이 최종 정지한다.

session이 없어도 HOLD와 직접 목표를 허용하지만 profile·익명 자세 preset은 현재 session과
`expectedSessionId`가 있어야 한다. STOP과 목표 CANCEL은 session 검증보다 먼저 처리한다.

## 높이 preset 모델

등록 사용자 Dashboard에는 자세별 높이와 custom preset을 하나의 목록으로 표시하지만 저장은
분리한다.

```text
Profile.sittingHeightCm ─┐
Profile.standingHeightCm ├─ 서버 합성 → EffectiveDeskPreset[]
desk_presets rows ───────┘
```

익명 session은 같은 자세 key에 기본 75/110cm를 합성하고 custom row는 반환하지 않는다.
자세별 높이를 `desk_presets`에 복제하지 않는다.

| 합성 필드 | 규칙 |
| --- | --- |
| `key` | `posture:sitting`, `posture:standing` 또는 custom preset ID |
| `kind` | `POSTURE` 또는 `CUSTOM` |
| `name` | 고정 자세 이름 또는 사용자 이름 |
| `heightCm` | 75~115cm |
| `editable` | 자세 항목 `false`, custom 항목 `true` |
| `heightPolicy` | `PROFILE` 또는 `ANONYMOUS_DEFAULT` |

사용자 custom preset 저장 필드는 다음과 같다.

| 저장 필드 | 규칙 |
| --- | --- |
| `id` | 서버 생성 ID |
| `profileId` | 소유 profile |
| `name` | trim 후 비어 있지 않으며 profile 안에서 고유 |
| `heightCm` | 75~115cm의 유한한 값 |

## preset 실행

Dashboard는 preset `key`와 `expectedSessionId`만 전달하며 profile ID와 높이를 실행 근거로
보내지 않는다.

```text
preset apply
  → expectedSessionId와 현재 session 비교
  → 등록이면 현재 profile 값·custom 소유권 조회
     익명이면 기본 자세 값 조회
  → AutomationService command lock
      → mode = MANUAL
      → 자동 generation 무효화
      → 진행 AUTO 이동 STOP
      → DeskController.set_target(heightCm)
```

화면에 남은 이전 session preset은 `409`로 거부한다. 실행 실패 후에도 MANUAL을 유지하며,
API 성공은 목표 접수 의미이고 실제 완료는 Desk snapshot으로 확인한다.

## 빈자리 park

안정 `VACANT`로 session이 끝나면 `PARK_WAITING`을 시작한다. 두 카메라의 fresh VACANT가
30초 동안 계속되고 활성 수동 의도·새 session이 없으며 장치가 준비된 경우에만 75cm 목표를
`PARK` source로 설정한다.

사람 후보, 새 session 후보, 수동 명령, camera·height·MQTT·relay 오류와 서버 종료는 대기나
진행 park를 취소하고 PARK 이동을 STOP한다. 서버 시작 직후 과거 VACANT나 retained height로
움직이지 않고 fresh VACANT 30초를 새로 요구한다.

실제 relay park는 fake 상태전이와 목표 기록, 제한된 장애물·의자 조건의 실물 검증을 통과한
뒤 활성화한다.
