# 03. 프로필과 작업 모드

## 사용자 결과

사용자는 profile의 기본 앉기 높이·서기 높이·LED 색상을 설정하고, 독서·공부처럼 이름을
붙인 작업 모드를 직접 만들 수 있다. 각 작업 모드는 앉기 높이, 서기 높이와 LED 색상을 한
묶음으로 저장한다.

기존 계획의 단일 높이 custom preset은 작업 모드로 대체한다. 일회성 직접 높이 입력은
유지한다. 이 task에서는 저장과 설정 API까지만 구현하며, 현재 session의 작업 모드 선택과
책상·LED 적용은 [책상 자동화](06-desk-automation.md)에서 구현한다.

## 현재 상태

- SQLite schema v3 migration과 현재 v4 schema에 `profile_modes` CRUD가 구현돼 있다.
- profile의 높이·LED는 내장 `기본` mode로 합성하고 custom 작업 모드를 별도 row로 저장한다.
- Dashboard 설정과 API는 profile 편집을 서버 current user 및 자동화 실행과 분리한다.
- 실제 책상·WLED 적용은 저장 CRUD의 완료 증거가 아니며 Task 06/09 범위다.

## 용어와 확정 정책

`mode`라는 이름을 두 의미로 사용하지 않는다.

| 이름 | 의미 | 값 예시 |
| --- | --- | --- |
| `controlMode` | 책상을 자동 또는 수동으로 제어하는 방식 | `AUTO`, `MANUAL` |
| `activityMode` | 활동별 앉기·서기 높이와 LED 설정 묶음 | 기본, 독서, 공부 |

- profile의 기존 `sitting_height_cm`, `standing_height_cm`, `led_color`는 삭제하지 않고
  수정 불가 이름 `기본`인 내장 작업 모드로 사용한다.
- 사용자 정의 작업 모드만 새 `profile_modes` row로 저장한다.
- 기본 모드는 삭제할 수 없고 이름도 바꾸지 않는다.
- custom 이름은 trim한 표시 이름과 별도 정규화 key로 profile 안의 중복을 판정한다.
- 단일 높이 custom preset과 `desk_presets` 테이블은 새로 만들지 않는다.
- profile 설정 CRUD는 현재 사용자 session, active 작업 모드, LED와 책상을 변경하지 않는다.
- 활성 작업 모드는 session 안에 값 snapshot으로 보관한다. 저장값 수정은 다음 선택 또는 다음
  session 시작부터 적용한다.
- 현재 활성화된 custom 작업 모드 삭제 `409` guard는 Task 06이 active mode snapshot을
  구현할 때 이 API에 연결한다. 현재 설정 API는 custom row를 삭제만 하며 session을 읽지 않는다.
- profile 삭제는 현재 DB transaction에서 custom 작업 모드를 cascade한다. 얼굴 embedding과
  장기 기억 삭제 orchestration은 각각 후속 Task 05·08과 통합할 때 완성한다.

## 저장 구조

현재 schema version 2 다음인 version 3 migration을 추가한다.

```text
profiles
  ├─ sitting_height_cm       # 내장 기본 모드
  ├─ standing_height_cm      # 내장 기본 모드
  └─ led_color               # 내장 기본 모드

profile_modes
  ├─ id
  ├─ profile_id → profiles.id ON DELETE CASCADE
  ├─ name
  ├─ normalized_name
  ├─ sitting_height_cm
  ├─ standing_height_cm
  └─ led_color
```

기본 모드와 custom row는 조회 시에만 합성한다.

```text
Profile의 높이·LED ─────────┐
profile_modes custom rows ──┴─→ EffectiveActivityMode[]
```

최소 합성 필드는 `key`, `kind`, `name`, `sittingHeightCm`, `standingHeightCm`,
`ledColor`, `editable`이다. 기본 모드는 안정된 `key="default"`, `kind="DEFAULT"`,
`editable=false`를 사용한다. custom은 서버 생성 ID를 key로 사용하고 `kind="CUSTOM"`,
`editable=true`다.

별도 범용 설정 JSON, 작업 모드별 규칙 엔진, 상속 구조와 versioned mode payload는 만들지
않는다. 단기 범위에서는 명시적인 세 필드가 가장 작고 검증하기 쉽다.

## 구현 단계

### schema와 모델

- [x] version 2 DB를 보존하는 SQLite version 3 migration과 schema 검증을 작성한다.
- [x] `profile_modes` foreign key, profile별 `normalized_name` unique 제약과 index를 정의한다.
- [x] `ActivityMode` create·update·effective 모델과 repository CRUD를 구현한다.
- [x] 두 높이 각각 75~115cm, 유한 숫자, LED `RRGGBB|null`, 이름 trim·빈 값·정규화 중복을
  DB와 Pydantic 경계에서 검증한다.
- [x] 기존 profile row를 별도 기본 mode row로 복제하지 않는다.

### service와 API

- [x] profile별 기본+custom 작업 모드 합성 목록을 제공한다.
- [x] custom 작업 모드 생성·수정·삭제 API를 구현한다.
- [x] 기본 모드 수정은 기존 profile PATCH를 사용하고 custom 모드 API에서 기본 key를 받지 않는다.
- [x] profile 삭제 후 custom mode가 남지 않고 없는 profile·mode 요청은 일관된 `404`가 되게 한다.
- [x] 설정 API가 current session, `AutomationService` 명령, `DeskController` 또는 WLED control을
  호출하지 않게 한다.

### 문서

- [x] API 요청·응답 예시와 version 3 migration 의미를 갱신한다.
- [x] 기본 모드 수정과 custom 모드 CRUD 위치를 Dashboard workflow에 명시한다.
- [x] `controlMode`와 `activityMode`를 화면에서 각각 `제어 방식`, `작업 모드`로 표시한다.

## 제외 범위

- 얼굴 embedding과 얼굴 등록 상태 저장
- 현재 사용자 session과 작업 모드 실행 권한 검증
- active custom mode 삭제 `409` 연결(task 06이 active snapshot을 구현할 때 이 API에 추가)
- 작업 모드 선택, control mode 전환, 실제 책상·LED 적용
- 범용 preset engine과 mode별 추가 plugin 설정

## 검증

- version 2 DB가 profile·height cache 데이터 손실 없이 version 3으로 한 번만 migration된다.
- migration 실패 시 transaction이 rollback되고 기존 DB가 손상되지 않는다.
- profile create·patch·조회에 `statureCm`이나 자세 유지 시간 필드를 추가하지 않는다.
- 기본 모드는 별도 row 없이 합성되고 삭제·이름 변경할 수 없다.
- 같은 profile의 정규화 중복 이름과 다른 profile 소유 mode 수정·삭제를 거절한다.
- 한 작업 모드에 앉기·서기 높이와 LED 색상이 함께 저장·조회된다.
- 설정값 수정이 현재 session의 active mode snapshot, LED와 Desk 목표를 즉시 바꾸지 않는다.
- profile 삭제 후 해당 custom mode가 남지 않는다.

## 완료 조건

- 서버 API만으로 profile 기본 모드와 custom 작업 모드 CRUD를 끝까지 수행할 수 있다.
- 기존 단일 높이 preset 없이 활동별 높이 두 개와 LED 색상을 일관되게 저장한다.
- 기존 SQLite version 2 데이터의 migration·rollback·제약이 자동 테스트로 검증된다.
- 후속 자동화와 Dashboard가 안정된 작업 모드 key와 소유권을 사용할 수 있다.
