# Dashboard 워크플로우

Dashboard는 profile 설정, 서버 상태 확인, 책상 수동 명령과 AI 상세 응답 표시를 담당한다.
재실·얼굴 식별, 등록·익명 session 결정, 자세 판정과 자동 높이 정책은 담당하지 않는다.

## 목표 화면 흐름

```text
DASHBOARD `/`
  ├─ 서버 현재 사용자·자세·제어 상태 표시
  ├─ 높이 preset·직접 제어
  ├─ AI 상세 응답 표시
  └─ 우측 상단 설정 → PROFILE_LIST `/settings/profiles`

PROFILE_LIST `/settings/profiles`
  ├─ profile 선택 → PROFILE_SETTINGS `/settings/profiles/:profileId`
  └─ 새 profile → `/settings/profiles/new`
                    → PROFILE_BASICS → DESK_SETTINGS
                    → FACE_ENROLLMENT → PROFILE_LIST

PROFILE_SETTINGS
  ├─ 이름·자세별 높이·조명 수정
  ├─ 사용자 높이 preset CRUD
  ├─ 얼굴 재등록·삭제
  └─ 저장 또는 취소 → PROFILE_LIST

VISION_DEBUG `/debug/vision`
```

서버가 다른 등록 사용자를 식별하거나 익명 session을 만들어도 profile 설정 화면을 자동으로 열거나 현재 편집 대상을
바꾸지 않는다.

## 현재 구현과 목표

현재 `App.tsx`는 첫 화면에 profile 목록을 표시하고, 카드를 누르면 React의
`selectedProfile`을 설정한 뒤 Dashboard로 이동한다. 이 값은 사용자 정보, 앉은/선 버튼과
WLED 색상에 사용된다.

`selectedProfile`은 서버 상태나 얼굴 식별과 연결되지 않았지만 화면에서는 현재 사용자처럼
취급된다. 목표 구현에서는 이를 `editingProfile` 성격의 설정 대상 상태로만 사용한다.

현재 얼굴 감지에 따라 특정 profile 화면을 자동으로 여는 기능은 없다. 목표 설계에서도
그 기능은 추가하지 않는다.

현재 첫 profile 선택 화면과 page enum을 유지하지 않고 `/`을 항상 메인 Dashboard로 만드는
전면 개편을 전제로 한다. 기존 API client와 HOLD/STOP 안전 동작은 재사용할 수 있지만,
`selectedProfile`로 메인 사용자와 제어 목표를 정하는 흐름은 제거한다.

## profile 목록

1. `GET /api/profiles`로 설정 가능한 profile을 조회한다.
2. 카드에는 이름, 앉은 높이, 선 높이와 얼굴 등록 여부를 표시한다.
3. 카드를 누르면 해당 profile 설정 화면을 연다.
4. 이 동작은 현재 사용자나 제어 모드를 변경하지 않고 책상을 움직이지 않는다.
5. 현재 문구인 “프로필을 선택하면 저장된 높이로 책상이 자동으로 이동합니다”는
   “프로필을 선택해 설정을 확인하거나 수정합니다”로 변경한다.

## 새 profile 등록

### 1단계: 기본 정보

| 입력 | 저장 |
| --- | --- |
| 이름 | `profiles.name` |

이 단계의 값은 draft다. 기본 정보만 입력한 불완전 profile은 아직 만들지 않는다.

### 2단계: 책상 설정

1. 앉은 높이와 선 높이를 75~115cm에서 입력한다.
2. 최신 높이가 `ONLINE`일 때만 “현재 높이 사용”으로 draft에 복사한다.
3. 자세 전환 확인 시간은 모든 사용자가 5초로 고정이며 입력값으로 받거나 profile에 저장하지
   않는다. 필요하면 읽기 전용 안내만 표시한다.
4. 제어 모드는 profile에 저장하지 않으며 새 등록·익명 session은 항상 `AUTO`로 시작한다.
5. 완료 시 기본 정보와 책상 설정을 한 profile 생성 요청으로 저장한다.
6. 생성된 profile ID로 얼굴 등록 단계로 이동한다.

### 3단계: 얼굴 등록

얼굴 등록 화면은 등록 명령과 상태 표시만 담당한다.

1. 카메라 준비 상태를 확인한다.
2. 사용자가 시작하면 얼굴 등록 session API를 호출한다.
3. `WAITING_FACE → CAPTURING → PROCESSING → SUCCEEDED`를 polling해 표시한다.
4. 실패 시 원인과 재시도, 진행 중이면 취소를 제공한다.
5. 완료 또는 건너뛰기 후 profile 목록으로 돌아간다.

등록 완료 자체로 현재 사용자를 지정하지 않는다. background 얼굴 식별이 이후 독립적으로
현재 사용자를 확정한다.

## 메인 Dashboard

| 영역 | 서버에서 읽을 상태 |
| --- | --- |
| 사용자 | 등록·익명 `CurrentUserSnapshot`과 연결 profile |
| 현재 자세 | 재실·자세·관측 시각과 freshness |
| 제어 모드 | `AUTO`/`MANUAL`, 전환 시각과 이유 |
| 높이 preset | 등록 사용자의 자세별·custom 목록 또는 익명 기본 75/110cm |
| 자동화 | 안정화 진행, 목표와 차단 이유 |
| 책상 | Desk·height·relay snapshot |
| 조명 | WLED snapshot |
| AI | 같은 Assistant `turnId`의 progress/tool/final phase와 화면용 상세 응답 |

익명 session은 “인식 실패” 대신 “게스트”와 기본 75/110cm를 표시하고 custom preset과
profile 설정은 제공하지 않는다. session이 없으면 사용자 전용 preset과 profile 값을 메인
제어에 사용하지 않지만 HOLD·직접 높이·STOP은 제공한다. mode·preset 요청에는 화면이 읽은
`expectedSessionId`를 자동으로 첨부하고 `409`이면 snapshot을 다시 읽는다. 사용자가 session
ID를 직접 입력하지 않는다. “SYSTEM ONLINE” 한 값으로 모든 기능을 표현하지 않고 기능별
상태를 표시한다.

current `sessionId`가 바뀌거나 없어지면 이전 session의 AI 상세 응답을 즉시 숨긴다. 늦게
완료된 이전 turn도 화면에 다시 나타나지 않게 turn의 session ID를 현재 snapshot과 비교한다.
같은 `turnId`에서는 증가하는 sequence만 적용해 진행 안내→tool 상태→최종 응답 순서를
유지하고, 취소·완료 뒤 도착한 낮은 sequence event는 버린다.

## profile 수정과 삭제

- profile 설정을 열어도 현재 사용자는 바뀌지 않는다.
- 얼굴 등록은 일반 profile PATCH와 분리해 재등록·삭제한다.
- 사용자 preset은 profile 설정 화면에서 별도 CRUD로 관리한다.
- 현재 사용자 profile을 삭제하면 서버가 먼저 자동화를 중지하고 책상을 STOP한 뒤 현재
  session을 제거한다. 신원 관측의 `UNKNOWN`과 session 없음은 서로 다른 상태다.
- profile 삭제는 profile, 얼굴 임베딩, 사용자 preset과 장기 기억 전체 삭제를 확인받는다.
  장기 기억 삭제에 실패하면 profile DB를 보존하고 재시도를 안내한다.

## Vision debug

일반 Dashboard와 분리된 운영·개발 화면에 다음을 표시한다.

- 두 카메라 preview
- raw detector와 안정화 결과
- frame 시각과 freshness
- 얼굴 등록 session
- 현재 사용자와 식별 근거
- `AUTO`/`MANUAL` 및 마지막 전환 이유
- 자동화 상태와 이동 금지 이유

얼굴 원본 또는 crop 저장 버튼은 기본 제공하지 않는다.
