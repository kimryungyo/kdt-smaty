# 07. Dashboard 워크플로우

## 사용자 결과

Dashboard에서 profile을 생성·수정하고 얼굴과 높이 preset을 관리할 수 있다. 메인 화면은
서버가 얼굴로 확정한 현재 사용자, 재실·자세, AUTO/MANUAL, 자동화 차단 이유와 책상 상태를
표시한다. profile 카드를 누르는 행위는 설정 화면만 열며 현재 사용자나 책상을 바꾸지 않는다.

## 현재 상태

- 첫 화면의 profile 카드를 누르면 React `selectedProfile`을 설정하고 메인 Dashboard로 간다.
- `selectedProfile`은 사용자 정보, 앉은·선 목표와 WLED profile 색상에 사용된다.
- 서버의 현재 사용자·Vision·자동화 API가 없어 자세와 AUTO 표시는 placeholder다.
- profile 생성은 기본 정보와 높이 2단계이며 키·유지 시간·얼굴·사용자 preset 화면이 없다.
- 얼굴 인식에 따라 특정 profile 화면을 자동으로 여는 코드는 현재도 없다.

## 화면 상태 원칙

React 상태를 최소한 다음 의미로 구분한다.

| 상태 | 소유·의미 |
| --- | --- |
| `editingProfile` | 사용자가 설정 화면에서 열어 둔 profile |
| profile draft | 아직 서버에 저장하지 않은 생성 입력 |
| current user snapshot | 서버가 얼굴로 결정한 read-only 사용자 session |
| automation snapshot | 서버가 소유한 mode·상태·차단 이유 |
| desk/WLED/Voice snapshot | 기능별 장치 상태와 freshness |

서버 현재 사용자가 바뀌어도 `editingProfile`이나 입력 draft를 자동으로 바꾸지 않는다. 반대로
profile 설정 화면을 열거나 닫아도 서버 session과 mode가 바뀌지 않는다.

## 목표 화면 흐름

```text
메인 Dashboard
  ├─ 서버 현재 사용자·자세·mode·책상·조명·AI 상태
  ├─ 현재 사용자 높이 preset과 수동 제어
  ├─ profile 관리 → 목록 → 생성 또는 설정
  └─ Vision debug

profile 생성
  → 이름·키
  → 앉은·선 높이·유지 시간·조명
  → 서버 profile 생성
  → 얼굴 등록 또는 건너뛰기
  → profile 목록

profile 설정
  → 기본 정보·책상 설정
  → 사용자 preset CRUD
  → 얼굴 재등록·삭제
```

생성 도중 profile row를 언제 만들지 명확히 한다. 권장 흐름은 기본 정보와 책상 설정을 draft로
유지하다 한 번에 profile을 만들고, 생성된 ID로 얼굴 등록을 시작하는 것이다. 얼굴 등록을
건너뛴 profile도 설정 데이터로는 유효하지만 현재 사용자로 인식될 수 없다.

## 구현 단계

### API client와 상태 분리

- [ ] profile·preset·얼굴 등록·현재 사용자·Vision·자동화 TypeScript 계약을 추가한다.
- [ ] `selectedProfile`을 설정 전용 `editingProfile`과 서버 current user로 분리한다.
- [ ] 기능별 polling 중복과 stale 응답 덮어쓰기를 방지한다.
- [ ] 사용자 의존 명령에 화면이 읽은 `expectedSessionId`를 전달한다.
- [ ] `409` session 충돌 시 명령 성공처럼 보이지 않게 새 snapshot을 다시 읽는다.

### profile 설정 흐름

- [ ] profile 목록 문구와 카드 동작을 “설정 열기” 의미로 변경한다.
- [ ] 이름·키, 앉은·선 높이, 자세 유지 시간과 조명 입력을 profile API에 연결한다.
- [ ] 최신 height가 ONLINE일 때만 “현재 높이 사용”을 draft에 복사한다.
- [ ] 사용자 preset 생성·수정·삭제 UI와 자세 preset의 비편집 표시를 구현한다.
- [ ] 얼굴 등록 진행, 취소·재시도·건너뛰기와 재등록·삭제를 연결한다.
- [ ] profile 삭제 시 삭제 범위와 활성 session 정지 가능성을 확인받는다.

### 메인 Dashboard

- [ ] 서버 current user와 연결 profile을 사용자 카드에 표시한다.
- [ ] 재실·자세·관측 age와 재검증·불확실 상태를 구분해 표시한다.
- [ ] AUTO/MANUAL, 자세 안정화 진행과 차단 이유를 실제 snapshot으로 표시한다.
- [ ] 현재 사용자 합성 preset과 mode 변경·직접 제어를 명령 API에 연결한다.
- [ ] 현재 사용자가 없으면 개인 preset과 profile 값을 실행 근거로 사용하지 않는다.
- [ ] WLED, Voice/AI, Vision, Desk의 기능별 연결 상태를 하나의 `SYSTEM ONLINE`과 분리한다.

### Vision debug

- [ ] 두 카메라 preview와 각 frame age·연결 상태를 표시한다.
- [ ] raw detector, 안정화 재실·자세·신원과 현재 사용자 session 근거를 표시한다.
- [ ] 얼굴 등록 session, mode 전환 이유와 자동화 차단 코드를 표시한다.
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

- profile 카드를 열고 수정해도 서버 current user, mode와 Desk 목표가 바뀌지 않는다.
- 서버 current user가 A→B로 바뀌어도 열려 있는 A 설정 form과 draft가 자동 변경되지 않는다.
- 오래된 A session 명령이 B에게 적용되지 않고 `409` 후 화면이 새 상태를 표시한다.
- 현재 사용자 없음·재검증·다중 사용자 상태에서 개인 preset 실행이 비활성 또는 거절된다.
- 얼굴 등록 성공 후에도 화면이 profile을 현재 사용자로 강제 지정하지 않는다.
- HOLD release, blur, page hide와 unmount에서 STOP 요청이 유지된다.
- API 오류, polling 단절과 out-of-order 응답에서 stale 값이 현재 값처럼 표시되지 않는다.
- TypeScript 검사와 production build가 통과하고 주요 화면 흐름을 브라우저에서 확인한다.

## 완료 조건

- profile 설정 대상과 서버 현재 사용자 상태가 코드·화면·문구에서 명확히 분리된다.
- profile 생성부터 얼굴 등록·preset 설정까지 Dashboard에서 끝까지 수행할 수 있다.
- 현재 사용자 preset, AUTO/MANUAL과 자동화 상태가 실제 서버 계약으로 동작한다.
- Dashboard를 닫거나 여러 개 열어도 서버의 얼굴 식별과 mode 소유권이 유지된다.
