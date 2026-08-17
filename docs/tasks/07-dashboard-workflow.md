# 07. Dashboard 워크플로우

## 사용자 결과

메인 화면 `/`은 서버가 재실·얼굴로 확정한 등록·익명 session, 재실·자세, 제어 방식,
작업 모드, 자동화 차단 이유, 높이 제어와 AI 응답을 표시한다. profile 생성·수정, 얼굴과 사용자
작업 모드 관리는 별도 설정 페이지에서 수행하며 설정 화면을 여는 행위는 현재 사용자나 책상을
바꾸지 않는다.

## 현재 상태

- 첫 화면의 profile 카드를 누르면 React `selectedProfile`을 설정하고 메인 Dashboard로 간다.
- `selectedProfile`은 사용자 정보, 앉은·선 목표와 WLED profile 색상에 사용된다.
- 서버의 현재 사용자·Vision·자동화 API가 없어 자세와 AUTO 표시는 placeholder다.
- profile 생성은 기본 정보와 높이 2단계이며 사용처 없는 키 입력, profile과 무관한 5초
  placeholder와 얼굴·작업 모드 미구현 영역이 섞여 있다.
- 얼굴 인식에 따라 특정 profile 화면을 자동으로 여는 코드는 현재도 없다.

## 화면 상태 원칙

React 상태를 최소한 다음 의미로 구분한다.

| 상태 | 소유·의미 |
| --- | --- |
| `editingProfile` | 사용자가 설정 화면에서 열어 둔 profile |
| profile draft | 아직 서버에 저장하지 않은 생성 입력 |
| current user snapshot | 서버가 재실·얼굴로 결정한 등록·익명 read-only session |
| automation snapshot | 서버가 소유한 control/activity mode·상태·차단 이유 |
| desk/WLED/Voice snapshot | 기능별 장치 상태와 freshness |
| Assistant turn snapshot | 서버가 정한 `turnId`·`sessionId`·phase·sequence와 화면 응답 |

서버 현재 사용자가 바뀌어도 `editingProfile`이나 입력 draft를 자동으로 바꾸지 않는다. 반대로
profile 설정 화면을 열거나 닫아도 서버 session과 두 mode가 바뀌지 않는다.

## 화면 구조와 전면 개편 원칙

현재 `App.tsx`의 page enum, 첫 profile 선택 화면과 legacy CSS를 유지하는 점진적 수정은 목표가
아니다. 기존 API client와 안전한 HOLD/STOP 동작은 재사용할 수 있지만 화면 구조는 다음
역할에 맞게 교체한다. 구체적인 router library는 구현 시 가장 작은 방식을 선택한다.

```text
메인 Dashboard `/`
  ├─ 서버 현재 사용자·자세·제어 방식·작업 모드·책상·조명·AI 상태
  ├─ 작업 모드 선택, 직접 높이·HOLD·STOP
  └─ 우측 상단 설정 버튼 → `/settings/profiles`

profile 관리 `/settings/profiles`
  ├─ profile 목록과 생성
  └─ profile 선택 → `/settings/profiles/:profileId`

profile 생성 `/settings/profiles/new`
  → 이름
  → 앉은·선 높이·조명
  → 서버 profile 생성
  → 얼굴 등록 또는 건너뛰기
  → `/settings/profiles`

profile 설정 `/settings/profiles/:profileId`
  → 기본 정보·책상 설정
  → 사용자 작업 모드 CRUD
  → 얼굴 재등록·삭제

Vision debug `/debug/vision`
```

생성 도중 profile row를 언제 만들지 명확히 한다. 권장 흐름은 기본 정보와 책상 설정을 draft로
유지하다 한 번에 profile을 만들고, 생성된 ID로 얼굴 등록을 시작하는 것이다. 얼굴 등록을
건너뛴 profile도 설정 데이터로는 유효하지만 현재 사용자로 인식될 수 없다.

## 구현 단계

### API client와 상태 분리

- [x] profile·activity mode·얼굴 등록·현재 사용자·Vision·자동화 TypeScript 계약을 추가한다. (07A: profile·activity mode만 완료)
- [x] 첫 profile 선택을 제거하고 `/`을 항상 메인 Dashboard로 표시한다.
- [x] 설정 route의 `editingProfile`과 서버 current user를 분리하고 `selectedProfile` 기반 제어를
  제거한다.
- [x] 기능별 polling 중복과 stale 응답 덮어쓰기를 방지한다.
- [ ] Assistant turn의 `turnId`, `sessionId`, progress/tool/final phase와 sequence 계약을
  TypeScript 모델에 추가한다.
- [x] control/activity mode 명령에 화면이 읽은 `expectedSessionId`를 전달한다. WLED의 stale-session
  검증은 현재 backend request model에 필드가 없어 Task 08B 의존으로 남긴다.
- [x] `409` session 충돌 시 명령 성공처럼 보이지 않게 새 snapshot을 다시 읽는다.

### profile 설정 흐름

- [x] profile 목록·생성·상세 설정 route를 연결한다. (메인 설정 버튼은 07B 범위)
- [x] 이름, 앉은·선 높이와 조명 입력을 profile API에 연결한다.
- [x] 사용자 키 입력·state와 자세 유지 시간 입력을 제거하고, 필요하면 “모든 사용자는 자세를
  5초 확인합니다”라는 읽기 전용 안내만 표시한다.
- [x] 최신 height가 ONLINE일 때만 “현재 높이 사용”을 draft에 복사한다.
- [x] 기본 작업 모드의 비편집 이름과 custom 작업 모드 생성·수정·삭제 UI를 구현한다.
- [x] 얼굴 등록 진행, 취소·재시도·건너뛰기와 재등록·삭제를 연결한다.
- [x] profile 삭제 확인에는 현 범위의 custom 작업 모드 cascade와 얼굴·Mem0 완전 삭제 미연결을 명시한다.

### 메인 Dashboard

- [x] 서버 current user와 연결 profile을 사용자 카드에 표시한다.
- [x] 익명 session을 오류가 아닌 “게스트”로 표시하고 작업 모드 없이 기본 75/110cm를 제공한다.
- [x] 재실·자세·관측 age와 얼굴 재확인 필요·불확실 상태를 구분해 표시한다.
- [x] `controlMode`를 `제어 방식`, `activityMode`를 `작업 모드`로 구분해 표시한다.
- [x] 작업 모드 선택과 control mode 변경·직접 제어를 명령 API에 연결한다.
- [x] MANUAL 작업 모드 선택은 LED만 바뀌고 책상이 움직이지 않음을 명확히 표시한다.
- [ ] 수동 LED 변경은 저장값이 아니라 현재 session override임을 표시한다. (Task 08B의 WLED
  `expectedSessionId` backend 계약 필요)
- [x] session이 없으면 개인 작업 모드와 profile 값을 사용하지 않되 HOLD·직접 높이·STOP은 제공한다.
- [x] control/activity mode 요청에는 화면이 읽은 `expectedSessionId`를 자동 첨부한다.
- [x] WLED, Voice/AI, Vision, Desk의 기능별 연결 상태를 하나의 `SYSTEM ONLINE`과 분리한다.
- [ ] current `sessionId`가 바뀌거나 없어지면 이전 AI 상세 응답을 즉시 화면에서 제거한다.
- [ ] `/api/assistant/latest`를 polling해 진행 안내, tool 실행 상태와 최종 응답을 같은
  `turnId` 안에서 순서대로 갱신하고 낮은
  sequence나 완료·취소된 turn의 늦은 event를 무시한다.
- [ ] session 없음·다중 상태의 비개인화 turn은 개인 profile 이름이나 memory 사용 상태로
  표시하지 않는다.

### Vision debug

- [ ] 두 카메라 preview와 각 frame age·연결 상태를 표시한다. (camera 상태·age와 browser preview URL 미구성 안내만 구현)
- [x] raw detector, 안정화 재실·자세·신원과 현재 사용자 session 근거를 표시한다.
- [ ] 얼굴 등록 session, control/activity mode 전환 이유와 자동화 차단 코드를 표시한다.
- [ ] 얼굴 원본·crop 저장이나 embedding 노출 기능은 추가하지 않는다.

## 명령 UX 원칙

- API `200`은 목표 접수이며 실제 도달과 다르므로 Desk 상태로 이동·완료를 표시한다.
- `409`는 현재 session이 바뀌었거나 명령 전제조건이 달라졌음을 안내한다.
- `503`은 장치·Vision·서버 기능별 미준비로 표시하고 profile 입력 오류와 구분한다.
- STOP은 현재 사용자 상태와 관계없이 누를 수 있어야 한다.
- 네트워크 단절 중 마지막 snapshot을 현재 상태처럼 보이지 않게 age와 stale 표시를 유지한다.

## 제외 범위

- 얼굴·자세 추론, 사용자 session과 자동화 정책 자체
- Dashboard에서 현재 사용자를 수동 지정하는 기능
- 얼굴 인식 시 profile 설정 화면 자동 이동
- 대규모 상태관리 framework나 디자인 시스템 도입
- 모바일 앱과 외부 네트워크 인증

## 검증

- profile 카드를 열고 수정해도 서버 current user, 두 mode와 Desk 목표가 바뀌지 않는다.
- `/` 새로고침은 profile 선택 화면이 아니라 메인 Dashboard를 표시한다.
- 설정 버튼으로 profile 설정을 열고 메인으로 돌아와도 session과 두 mode가 유지된다.
- 서버 current user가 A→B로 바뀌어도 열려 있는 A 설정 form과 draft가 자동 변경되지 않는다.
- 오래된 A session 명령이 B에게 적용되지 않고 `409` 후 화면이 새 상태를 표시한다.
- session 없음·다중·count 불일치에서 개인 작업 모드 실행이 비활성 또는 거절된다.
- stale session activity mode는 `409` 후 current user·automation snapshot을 다시 읽는다.
- 얼굴 등록 성공 후에도 화면이 profile을 현재 사용자로 강제 지정하지 않는다.
- HOLD release, blur, page hide와 unmount에서 STOP 요청이 유지된다.
- API 오류, polling 단절과 out-of-order 응답에서 stale 값이 현재 값처럼 표시되지 않는다.
- A→B 또는 session 종료 즉시 A의 AI 상세 응답이 화면에서 사라진다.
- 진행 안내→tool 상태→최종 응답이 같은 `turnId`로 갱신되고 out-of-order event가 화면을
  되돌리지 않는다.
- session 교대 후 늦은 TTS·tool·final event가 새 사용자 화면에 다시 나타나지 않는다.
- TypeScript 검사와 production build가 통과하고 주요 화면 흐름을 브라우저에서 확인한다.

## 완료 조건

- profile 설정 대상과 서버 현재 사용자 상태가 코드·화면·문구에서 명확히 분리된다.
- 메인 `/`과 별도 profile 설정 route가 분리되고 현재의 profile 선택 기반 화면 흐름이 제거된다.
- profile 생성부터 얼굴 등록·작업 모드 설정까지 Dashboard에서 끝까지 수행할 수 있다.
- 현재 사용자 작업 모드, AUTO/MANUAL과 자동화 상태가 실제 서버 계약으로 동작한다.
- Dashboard를 닫거나 여러 개 열어도 서버의 얼굴 식별과 두 mode 소유권이 유지된다.
