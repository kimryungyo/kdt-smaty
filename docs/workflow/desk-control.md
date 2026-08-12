# 책상 제어 모드와 높이 프리셋

`AutomationService`는 현재 사용자 session의 `AUTO`/`MANUAL` 모드와 상위 사용자 의도를
소유한다. `DeskController`는 센서·릴레이 안전, 실제 목표 이동과 STOP을 계속 소유한다.

## 제어 모드

| 모드 | 의미 |
| --- | --- |
| `AUTO` | 안정화된 `SITTING`/`STANDING`에 따라 profile 높이를 선택 |
| `MANUAL` | Vision은 관측만 하고 preset·직접 목표·HOLD 같은 사용자 명령만 허용 |

현재 사용자가 없으면 runtime 모드도 없으며 책상은 정지한다. 얼굴로 사용자가 확정되면 새
session은 항상 `AUTO`로 시작한다.

```text
현재 사용자 없음
  → 얼굴로 사용자 확정 → AUTO

AUTO
  ├─ preset / 직접 목표 / HOLD / STOP → MANUAL
  └─ 이탈·신원 상실 → STOP 후 session 종료

MANUAL
  ├─ 명시적 “자동 모드” → STOP → 자세 안정화 초기화 → AUTO
  └─ 이탈·신원 상실 → STOP 후 session 종료
```

- 시간 경과만으로 `MANUAL → AUTO` 전환하지 않는다.
- 수동 명령이 장치 상태 때문에 실패해도 `MANUAL`을 유지한다.
- AUTO 전환 직후 이전 자세 snapshot으로 움직이지 않고 자세를 다시 안정화한다.
- `UNKNOWN`, `UNREGISTERED`, `MULTIPLE`, `VACANT`에서는 모드와 관계없이 STOP한다.
- 여러 Dashboard가 열려도 서버 mode snapshot이 기준이다.

## AUTO 자세 제어

```text
VisionSnapshot
  + CurrentUserSnapshot
  + Profile
  + DeskSnapshot
  → AutomationService
      → freshness·신원·단일 사용자 검사
      → 자세 유지 시간 검사
      → 같은 목표 반복 억제
      → DeskController.set_target()
```

| 안정된 관측 | 동작 |
| --- | --- |
| 현재 사용자 + `SITTING` | `sittingHeightCm` 목표 |
| 현재 사용자 + `STANDING` | `standingHeightCm` 목표 |
| `VACANT` | STOP 후 사용자 session 종료 |
| 자세 `UNKNOWN` | 자동 이동 STOP, 새 목표 금지 |
| 미등록·다중 사용자 | STOP, 새 목표 금지 |

사용자가 일어났다고 즉시 움직이지 않는다. 동일 사용자의 재실과 `STANDING`이 profile의
유지 시간 동안 이어진 뒤 선 높이를 한 번 설정한다. 다시 앉을 때도 같은 안정화를 거친다.
사용자·자세·모드 변경과 frame 만료는 후보와 timer를 초기화한다.

자동화 상태는 mode와 별도로 `WAITING_USER`, `OBSERVING`, `READY`, `MOVING`, `MANUAL`,
`BLOCKED`를 제공한다. `BLOCKED`가 되면 자동화가 시작한 이동을 STOP한다.

## 수동 명령

HOLD, STOP, 직접 목표와 모든 preset 클릭은 먼저 `MANUAL`로 전환하고 진행 중 자동 의도를
무효화한다. 자동화와 Dashboard는 relay를 직접 호출하지 않고 `DeskController` 공개
메서드만 사용한다.

HOLD는 현재처럼 200ms마다 갱신하고 pointer/keyboard release, blur, page hide와 unmount에서
STOP한다. 요청 유실 시 `DeskController` watchdog과 ESP32 pulse timeout이 최종 정지한다.

## 높이 preset 모델

Dashboard에는 자세별 높이와 사용자 preset을 하나의 목록으로 표시하지만 저장은 분리한다.

```text
Profile.sittingHeightCm ─┐
Profile.standingHeightCm ├─ 서버 합성 → EffectiveDeskPreset[]
desk_presets rows ───────┘
```

앉은/선 높이를 `desk_presets`에 복제하지 않는다. 복제하면 profile 수정 시 값이 어긋나고
자동 자세 정책이 삭제 가능한 사용자 row에 의존하게 된다.

합성 목록 예시:

```text
[앉은 높이 · 78cm] [선 높이 · 105cm] [영화 · 90cm] [독서 · 85cm]
```

| 합성 필드 | 규칙 |
| --- | --- |
| `key` | `posture:sitting`, `posture:standing` 또는 사용자 preset ID |
| `kind` | `POSTURE` 또는 `CUSTOM` |
| `name` | 고정 자세 이름 또는 사용자 이름 |
| `heightCm` | 75~115cm |
| `editable` | 자세 항목 `false`, 사용자 항목 `true` |

자세별 높이는 profile 기본 설정에서 수정한다. 사용자 preset은 별도 `desk_presets`로 저장한다.

| 저장 필드 | 규칙 |
| --- | --- |
| `id` | 서버 생성 ID |
| `profileId` | 소유 profile |
| `name` | trim 후 비어 있지 않으며 profile 안에서 고유 |
| `heightCm` | 75~115cm의 유한한 값 |

메인 Dashboard는 설정 대상으로 연 profile이 아니라 서버가 얼굴로 확정한 현재 사용자의
합성 목록만 표시한다.

## preset 실행

Dashboard는 preset `key`만 전달하며 profile ID와 높이를 실행 근거로 보내지 않는다.

```text
preset apply
  → CurrentUser fresh/RECOGNIZED 확인
  → posture key면 현재 profile 고정 높이 조회
     custom key면 row와 profile 소유권 확인
  → AutomationService command lock
      → mode = MANUAL
      → 자동 generation 무효화
      → 진행 중 이동 STOP
      → DeskController.set_target(heightCm)
```

화면에 남은 다른 사용자의 preset 요청은 `409`로 거부한다. 실행 실패 후에도 MANUAL을
유지하며, API 성공은 목표 접수 의미이고 실제 완료는 Desk snapshot으로 확인한다.

## 대표 시나리오

### 사용자가 일어남

```text
RECOGNIZED + AUTO + SITTING
  → STANDING 후보
  → 동일 사용자·재실·STANDING 유지
  → standingHeightCm 설정
```

### 영화 preset

```text
[영화 · 90cm] 클릭
  → 현재 사용자 소유권 확인
  → MANUAL + 기존 자동 이동 STOP
  → 90cm 목표
  → 자세가 바뀌어도 자동 이동 없음
  → 명시적 AUTO 선택 시 자동화 복귀
```

### 사용자 이탈

```text
AUTO 또는 MANUAL
  → VACANT 안정화
  → STOP
  → CurrentUser profile 해제와 mode 종료
  → 재식별 시 새 AUTO session
```
