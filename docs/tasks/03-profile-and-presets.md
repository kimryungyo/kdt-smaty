# 03. 프로필과 높이 프리셋

## 사용자 결과

사용자는 profile에 이름, 키, 앉은 높이, 선 높이와 자세 유지 시간을 저장할 수 있다.
“영화 90cm” 같은 높이 preset을 별도로 만들고 수정·삭제할 수 있으며, 앉은·선 높이도
Dashboard의 preset 목록에 함께 표시될 수 있다.

이 작업에서는 설정과 저장까지만 완성한다. preset 클릭으로 책상을 움직이는 동작은
[책상 자동화](06-desk-automation.md)에서 현재 사용자 session과 함께 구현한다.

## 현재 상태

- SQLite version 1의 `profiles`에는 이름, 앉은·선 높이와 LED 색상만 있다.
- profile 기본 CRUD와 FastAPI API는 구현돼 있다.
- 사용자 키 입력은 영속화되지 않으며 자세 유지 시간은 5초 placeholder다.
- 사용자 정의 preset 저장소와 API는 없다.
- Dashboard의 앉은·선 높이 버튼은 화면에서 선택한 profile 값을 직접 사용한다.

## 데이터 결정

구현 전에 [상태·워크플로우 계약 확정](01-workflow-contracts.md)과 함께 다음을 확정한다.

- `stature_cm`의 허용 범위, 필수 여부와 기존 profile migration 기본값
- 자세 유지 시간을 앉음·섬 공통 한 값으로 둘지 자세별로 둘지
- 유지 시간의 허용 범위와 기본 5초 처리
- profile 안에서 사용자 preset 이름을 대소문자·공백 기준으로 어떻게 고유하게 볼지
- profile 삭제 시 preset과 얼굴 임베딩을 같은 transaction에서 cascade할지 service에서
  명시적으로 삭제할지

얼굴 등록 여부는 `profiles`에 중복 boolean으로 저장하지 않는다. 얼굴 저장소가 생기는
[얼굴 식별과 사용자 세션](05-face-identity-session.md)에서 연관 데이터 존재 여부로 파생한다.

## 저장 구조

```text
profiles
  ├─ stature_cm
  ├─ sitting_height_cm
  ├─ standing_height_cm
  └─ posture_hold_seconds

desk_presets
  ├─ id
  ├─ profile_id → profiles.id
  ├─ name
  └─ height_cm
```

앉은·선 높이는 `desk_presets`에 row로 복제하지 않는다. 실행·표시 시 다음처럼 합성한다.

```text
Profile.sitting_height_cm  ─┐
Profile.standing_height_cm ─┼─ EffectiveDeskPreset[]
desk_presets rows          ─┘
```

합성된 자세 preset은 안정된 key(`posture:sitting`, `posture:standing`)와
`editable=false`를 사용한다. 사용자 preset만 별도 ID와 `editable=true`를 가진다.

## 구현 단계

### schema와 모델

- [ ] 기존 DB를 보존하는 SQLite version 2 migration과 schema 검증을 작성한다.
- [ ] profile 모델·create·update·repository에 새 필드를 추가한다.
- [ ] `desk_presets` 제약, foreign key와 index를 정의한다.
- [ ] `DeskPreset` create·update 모델과 repository CRUD를 구현한다.
- [ ] 높이 75~115cm, 유한 숫자, 이름 trim·빈 값·중복을 DB와 Pydantic 경계에서 검증한다.

### service와 API

- [ ] profile 설정 API가 새 필드를 생성·조회·부분 수정하도록 확장한다.
- [ ] profile별 preset 목록·생성 및 preset 수정·삭제 API를 구현한다.
- [ ] profile 앉은·선 높이와 사용자 row를 합성하는 순수 service를 구현한다.
- [ ] 합성 목록 API는 설정 대상 profile과 현재 사용자 실행 대상을 혼동하지 않게 분리한다.
- [ ] profile 삭제 시 연관 preset이 남지 않고 없는 profile 요청은 일관된 `404`가 되게 한다.

### 문서

- [ ] API 요청·응답 예시와 migration 의미를 갱신한다.
- [ ] 앉은·선 높이 수정 위치와 사용자 preset 수정 위치가 다름을 Dashboard workflow에
  명시한다.

## 제외 범위

- 얼굴 임베딩과 얼굴 등록 상태 저장
- 현재 사용자 session과 preset 소유권 검증
- preset 실행, mode 전환과 실제 책상 이동
- profile 생성·수정 Dashboard 화면 연결

## 검증

- version 1 DB가 데이터 손실 없이 새 schema로 한 번만 migration된다.
- migration 실패 시 transaction이 rollback되고 기존 DB가 손상되지 않는다.
- profile create·patch·조회 round-trip에서 키와 유지 시간이 보존된다.
- 같은 profile의 중복 이름과 다른 profile 소유 preset 수정·삭제를 거절한다.
- 앉은·선 높이 수정이 별도 row 생성 없이 합성 목록에 즉시 반영된다.
- profile 삭제 후 해당 사용자 preset이 남지 않는다.
- 설정 CRUD가 현재 사용자, 제어 mode와 `DeskController`를 호출하지 않는다.

## 완료 조건

- 서버 API만으로 profile 기본 설정과 사용자 preset CRUD를 끝까지 수행할 수 있다.
- 앉은·선 높이와 사용자 preset이 중복 저장 없이 하나의 유효 목록으로 합성된다.
- 기존 SQLite 데이터의 migration·rollback·제약이 자동 테스트로 검증된다.
- 후속 얼굴 저장소와 자동화가 profile·preset 소유권을 안정적으로 참조할 수 있다.
