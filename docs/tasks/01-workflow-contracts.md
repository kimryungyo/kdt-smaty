# 01. 상태·워크플로우 계약 확정

## 상태

**완료** — 2026-08-17 작업 모드·서비스 정책 보완

이 문서는 후속 Vision, 얼굴 식별, 사용자 session, 자동화, Dashboard와 Voice 구현이 따를
상태·전이·명령 계약을 확정한다. 구체적인 모델과 실측 threshold 보정은 후속 task에서 하되,
안전 정책을 다시 해석하거나 새로 만들지 않는다.

Voice 구현의 문서 우선순위는 범위로 나눈다.

- 이 문서는 사용자·재실 session, `expectedSessionId`, 물리 제어와 STOP 안전 계약의 기준이다.
- [Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)은
  `VoicePipeline`, model·STT·TTS·VAD, function tool, SDK session과 Mem0 adapter 구현의 기준이다.
- 기존 AI 스피커 설계와 Agents SDK 문서가 충돌하면 교체 범위에서는 Agents SDK 문서를
  따른다. Agents SDK 구현도 이 문서의 사용자 session과 물리 안전 계약을 우회할 수 없다.

## 사용자 결과

등록 여부와 관계없이 책상 앞에 한 사람이 안정적으로 머물면 사용자 session이 시작된다.
등록 얼굴이면 profile 높이를, 얼굴이 없거나 등록되지 않았으면 익명 기본 높이를 사용한다.
얼굴이 잠시 보이지 않아도 단일 재실이 이어지면 session과 자동화를 유지하며, 사람 교대,
다중 사용자, 이탈과 장치 오류에서는 아래 결정표대로 자동 이동과 session을 처리한다.

## 공통 용어와 상태 축

서로 다른 의미를 한 enum으로 합치지 않는다.

| 축 | 상태 | 소유자 |
| --- | --- | --- |
| 신원 관측 | `MATCHED`, `UNKNOWN_FACE`, `AMBIGUOUS`, `NO_FACE`, `UNKNOWN` | Identity/Vision |
| 재실 | `PRESENT_SINGLE`, `VACANT`, `MULTIPLE`, `UNKNOWN` | Vision |
| 자세 | `SITTING`, `STANDING`, `UNKNOWN` | Vision |
| 사용자 session | 없음, `REGISTERED`, `ANONYMOUS` | CurrentUserSessionService |
| 제어 방식 | session의 `controlMode=AUTO`, `MANUAL` 또는 없음 | AutomationService |
| 작업 모드 | 등록 session의 기본·custom `activityMode` 또는 없음 | AutomationService |
| 자동화 | `WAITING_USER`, `OBSERVING`, `READY`, `MOVING`, `MANUAL`, `BLOCKED`, `PARK_WAITING`, `PARKING` | AutomationService |

`UNKNOWN_FACE`는 고품질 얼굴이 현재 등록 template과 일치하지 않는 관측이며,
`NO_FACE`는 fresh frame에 얼굴이 보이지 않는 관측이다. 얼굴 품질 부족, model 오류와 stale
frame을 `UNKNOWN_FACE`로 승격하지 않는다.

`ANONYMOUS`는 오류나 공용 profile이 아니다. `profileId=null`, `activityMode=null`과 기본
높이 정책을 가진 정상적인 process-memory session이다. 익명 profile row, 공용 mode row와 장기 기억
namespace는 만들지 않는다.

Voice에서 사용하는 session이라는 말은 다음 세 수명을 구분한다.

| 구분 | key와 수명 | 소유자 |
| --- | --- | --- |
| 책상 사용자 session | 현재 사용자에게 발급한 `sessionId`, 재실·신원 전환까지 | `CurrentUserSessionService` |
| Agent 대화 session | 책상 `sessionId`에 연결된 대화 history, 사용자 session 종료까지 | Voice memory adapter |
| Voice audio 실행 | Wake Word와 조건부 follow-up 묶음, 한 번의 짧은 `VoicePipeline` 실행 | Voice runtime |

한 책상 사용자 session에는 여러 Voice audio 실행과 Assistant turn이 들어갈 수 있다. Agent
대화 session은 그 실행들 사이의 단기 문맥만 유지하며 profile 장기 기억과 동일하지 않다.

## 시간과 freshness

- 내부 duration, 안정화와 timeout 판단은 monotonic clock을 사용한다.
- API에는 UTC wall-clock 시각과 필요하면 age를 제공한다.
- 신원, 카메라별 재실과 자세는 각각 `observedAt`, `expiresAt` 또는 동등한 freshness 근거를
  가진다. 하나의 공통 `observedAt`으로 두 카메라를 대표하지 않는다.
- 같은 `capturedAt`의 frame을 여러 번 처리해 연속 관측 수를 채우지 않는다.
- threshold는 설정 한 곳에 두고 실측으로 좁힐 수 있지만, 안전 범위를 코드 곳곳에서 다르게
  해석하지 않는다.

초기 제품 정책값은 다음과 같다.

| 설정 | 초기값 | 의미 |
| --- | ---: | --- |
| `presenceConfirmationSeconds` | 3초 | session 시작 전 단일 재실 안정화 |
| `postureTransitionHoldSeconds` | 5초 | 최초 이동 이후 모든 사용자의 자세 전환·AUTO 재활성화 안정화 |
| `initialAutoMoveDelaySeconds` | 2초 | 첫 session 생성 후 최초 자동 목표 지연 |
| `unknownFaceTransitionSeconds` | 3초 | 등록 사용자에서 익명 사용자로 바꿀 고품질 미등록 얼굴 안정화 |
| `vacantParkDelaySeconds` | 30초 | session 종료 후 75cm park 대기 |
| `anonymousSittingHeightCm` | 75cm | 익명 앉은 높이 |
| `anonymousStandingHeightCm` | 110cm | 익명 선 높이 |
| `vacantParkHeightCm` | 75cm | 빈자리 주차 높이 |

높이 기본값은 profile DB가 아니라 자동화 설정에 둔다. 제어 하한 75cm와 익명 앉은 높이가
현재 같더라도 서로 다른 의미의 설정으로 유지하며 모든 목표는 기존 75~115cm 안전 검증을
통과한다.

session 시작은 `PRESENT_SINGLE`과 자세를 3초 확인하고 session 생성 뒤 2초를 더 기다리므로,
최초 자동 목표까지 정상 조건에서 총 5초가 걸린다. 이 최초 흐름에
`postureTransitionHoldSeconds`를 다시 더하지 않는다. 5초 자세 전환 확인은 최초 목표 이후
앉음↔섬 전환과 같은 session에서 사용자가 `MANUAL`에서 `AUTO`를 다시 선택한 경우에 적용한다.

## 두 카메라 관측 결합

첫 구현은 사람 Re-ID 없이 단일 책상·단일 사용자 조건만 지원한다.

```text
상단 책상 ROI: 몸체 또는 얼굴 한 명
AND
하단 책상 ROI: 하체 한 명
AND
두 frame과 detector 결과가 fresh하고 허용 시각 차이 안
→ PRESENT_SINGLE 후보
```

상단 얼굴과 몸체가 함께 검출돼도 두 사람으로 합산하지 않고 한 사람의 존재 근거로 결합한다.
몸체 또는 얼굴 한쪽만 상단에서 보여도 하단의 fresh한 하체 한 명과 결합할 수 있다. 두
카메라 count가 다르거나 어느 자세가 한 사람에게 귀속되는지 불명확하면 결합 상태를
사용 불가로 만들고 새 AUTO 목표를 금지한다.

ROI, 허용 frame 시각 차이와 detector threshold의 실제 수치는 카메라 설치 후 task 04에서
측정하지만, 두 카메라 singleton과 freshness를 생략하는 운영 fallback은 두지 않는다.

## session 시작

### 등록 사용자

동일 등록 얼굴이 여러 distinct fresh frame에서 안정화되고 `PRESENT_SINGLE`과 결합되면 새
`REGISTERED` session을 만든다.

```text
session 없음
  + PRESENT_SINGLE·자세 3초 안정화
  + MATCHED(A) 안정화
  → 새 sessionId, REGISTERED(A), controlMode=AUTO, activityMode=기본
  → A 기본 LED 적용
  → 2초 최초 이동 지연
  → 조건 유지 시 A의 자세 높이 목표
```

### 익명 사용자

등록 사용자가 확정되지 않아도 `PRESENT_SINGLE`과 자세가 3초 안정화되면 익명 session을
만든다. 미등록 얼굴 확정은 session 시작 조건이 아니므로 얼굴이 전혀 보이지 않아도 된다.

```text
session 없음
  + PRESENT_SINGLE·자세 3초 안정화
  + 등록 사용자 미확정
  → 새 sessionId, ANONYMOUS, controlMode=AUTO, activityMode 없음
  → 2초 최초 이동 지연
  → SITTING이면 75cm, STANDING이면 110cm
```

2초 대기 중 자세 변경, 사람 후보 이탈, `MULTIPLE`, count 불일치, frame 만료, session 전환,
수동 명령 또는 장치 오류가 발생하면 최초 목표를 취소한다. 자세가 `UNKNOWN`이면 session만
시작하고 자세가 새로 안정화될 때까지 이동하지 않는다.

## session 연속성과 얼굴 관측

fresh한 `PRESENT_SINGLE`이 끊기지 않는 동안 얼굴이 계속 보이지 않아도 현재 session,
profile 귀속, control mode와 activity mode를 유지한다. v1에는 주기적 얼굴 재확인 timeout을 두지 않으며 등록
session의 AUTO 자세 제어와 Agent 대화 session도 계속 사용할 수 있다. 단, 아래의
`MULTIPLE`·count 불일치 정책에서는 보존만 하고 개인화에 접근하지 않는다.

이 정책은 A와 B가 `VACANT`나 `MULTIPLE` 없이 교대하고 B 얼굴도 보이지 않으면 A session이
남을 수 있다는 알려진 제한이 있다. 단기 단일 책상 프로젝트에서는 수용하며 실측에서 반복
문제가 확인될 때 tracking, Re-ID 또는 얼굴 재확인 lease를 추가한다.

## 사용자 전환

### 익명에서 등록 사용자로

익명 session 중 A 얼굴이 안정적으로 확인되면 profile만 제자리에서 바꾸지 않고 새
`REGISTERED` session ID를 발급한다.

- 익명 control mode가 `AUTO`이면 A의 기본 작업 모드를 활성화하고 현재 fresh 자세를 이어
  받아 기본 모드 높이로 즉시 목표를 교체한다. `initialAutoMoveDelaySeconds`를 다시 적용하지 않는다.
- 기존 익명 AUTO 목표가 진행 중이면 `DeskController.set_target()`의 기존 안전 전환대로
  STOP 확인 후 현재 높이에서 새 목표와 방향을 계산한다.
- 익명 control mode가 `MANUAL`이면 새 등록 session도 `MANUAL`을 보존하고 A의 기본 작업
  모드와 LED만 적용한다. 진행 중인 명시적 수동 의도를 AUTO가 덮어쓰지 않는다.
- 익명 session ID, 작업 모드 권한과 Agents SDK 대화 history는 새 등록 session에
  상속하지 않는다.

### 등록 사용자 A에서 B로

B의 한 frame 후보만으로 전환하지 않는다. B가 안정화되면 A의 AUTO generation과 진행
AUTO 이동을 STOP하고 A session을 종료한 뒤 새 B session을 `AUTO`와 B 기본 작업 모드로
시작한다. A의 두 mode, 자세 후보와 목표는 상속하지 않으며 B 자세를 처음부터 안정화한다.

### 등록 사용자 A에서 익명 사용자로

A 얼굴이 안 보이거나 품질이 낮다는 이유로 전환하지 않는다. fresh한 단일 재실에서 고품질
`UNKNOWN_FACE`가 3초 동안 안정적으로 이어질 때 A의 AUTO 이동과 session을 종료하고 새
익명 `AUTO` session을 시작한다. 한 frame false negative, `NO_FACE`, `AMBIGUOUS`, model
오류와 stale frame은 이 전환 근거가 아니다. A와 미등록 얼굴이 동시에 보이면 익명 전환이
아니라 `MULTIPLE` 정책을 적용한다.

### 다중 사용자와 count 불일치

`MULTIPLE`, 카메라 count 불일치 또는 관측 연속성 단절에서는 현재 session과 두 mode를
유지하되 AUTO generation과 진행 AUTO 이동을 즉시 STOP하고 자세 후보를 초기화한다.
Dashboard HOLD, 직접 목표와 STOP은 계속 허용한다.

- 등록 session은 같은 얼굴을 다시 안정적으로 확인한 뒤 AUTO 차단을 해제한다.
- 익명 session은 fresh한 `PRESENT_SINGLE`을 다시 3초 안정화한 뒤 AUTO 차단을 해제한다.
- 다른 등록 얼굴이 확인되면 해당 사용자 새 session으로 전환한다.
- 기존 Agent 대화 session은 보존하지만 차단 중에는 읽거나 쓰지 않고 Mem0에도 접근하지
  않는다. 일반 비개인화 질문은 해당 Wake Word·follow-up 묶음에만 유효한 임시 session으로
  처리한다.

## 제어 방식, 작업 모드와 명시적 수동 제어

- 모든 등록·익명 새 session은 `controlMode=AUTO`로 시작한다. 단, 익명 `MANUAL`에서 동일 사용자의
  등록 identity만 확정되는 전환은 명시적 수동 의도를 보존한다.
- 등록 session은 profile의 기본 작업 모드를 active snapshot으로 선택하고 기본 LED를 적용한다.
  익명 session은 activity mode가 없고 75/110cm 기본 높이를 사용한다.
- HOLD와 직접 목표는 먼저 `MANUAL`로 전환하고 AUTO generation을 무효화하되 active 작업
  모드는 유지한다.
- 사용자 STOP은 활성 session을 `MANUAL`로 만들지만 Vision·장치 안전 STOP은 기존 control
  mode를 보존한다. session 종료 STOP은 두 mode를 제거한다.
- 시간 경과로 `MANUAL → AUTO` 전환하지 않는다.
- 명시적 AUTO 재활성화는 같은 session에서 사용자가 `MANUAL`에서 `AUTO`를 다시 선택하는
  명령이다. 진행 이동 STOP, 자세 후보 초기화 후 fresh 자세를 5초 다시 안정화한다.
- session이 없어도 Dashboard HOLD, 직접 높이와 STOP을 허용한다. 이 명령은 session이나
  mode를 새로 만들지 않는다.
- 작업 모드 전환은 control mode를 바꾸지 않는다. AUTO에서는 fresh 자세로 새 mode 높이를
  안전하게 재평가하고, MANUAL에서는 LED만 적용하며 책상을 움직이지 않는다.
- 수동 LED 변경은 session override이며 저장 mode를 바꾸지 않는다. 다음 작업 모드 전환이나
  다음 session 시작에서 저장 색상을 다시 적용한다.

Vision 불확실성은 AUTO만 차단한다. height sensor stale, MQTT/relay 미준비, 제어 범위와
ESP32 안전 오류는 AUTO와 수동 이동을 모두 차단한다. STOP은 어느 경우에도 접수한다.

## session 종료와 빈자리 park

다음 사건은 session을 종료한다.

- 안정화된 `VACANT`
- 다른 등록 또는 익명 사용자 session으로 전환
- 얼굴 등록·재등록·얼굴 삭제 시작
- 활성 profile 삭제
- 서버 종료·재시작

`VACANT` 종료 순서는 AUTO generation 무효화, 진행 AUTO 이동 STOP, 자세 후보 초기화,
두 mode와 session 제거, WLED OFF best-effort 요청, park 후보 시작이다. WLED 실패는 STOP과
session 종료를 rollback하지 않는다.

session 종료 뒤 두 카메라의 fresh한 `VACANT`가 30초 동안 계속되고 새 session·활성 수동
의도가 없으며 장치가 준비된 경우에만 75cm `PARK` 목표를 만든다. 다음 사건은 park 대기 또는 진행
이동을 즉시 취소하고 진행 PARK 이동을 STOP한다.

- 어느 카메라에서든 사람 후보가 나타남
- 새 session 후보 또는 session이 시작됨
- Dashboard HOLD, 직접 목표 또는 STOP
- camera, height, MQTT 또는 relay가 stale/오류가 됨
- 서버 종료

서버 시작 직후 과거 `VACANT`나 retained height로 park하지 않는다. lifecycle 준비 이후
fresh한 `VACANT` 30초를 새로 관측해야 한다.

## 얼굴 등록과 profile lifecycle

- 얼굴 등록·재등록·삭제 시작은 새 identity 발행을 중지하고, 활성 AUTO를 STOP한 뒤 현재
  session과 후보를 종료한다.
- 성공·실패·취소와 무관하게 background 식별을 새로 통과해야 다음 session이 생긴다.
- 얼굴 등록 성공 자체는 profile을 현재 사용자로 만들지 않는다.
- 일반 profile·작업 모드 설정 CRUD는 현재 session, active snapshot, LED와 책상을 움직이지 않는다.
- active 작업 모드 설정값은 다음 선택 또는 다음 session부터 반영하고 active custom mode
  삭제는 `409`로 거절한다.
- 활성 profile 삭제는 새 명령 차단, AUTO generation 무효화, STOP 시도, session 종료 후
  `profile:<profile_id>` 장기 기억 전체 삭제를 먼저 완료하고 얼굴·작업 모드와 profile row를
  삭제한다. 기억 삭제가 실패하면 profile DB를 유지하고 `503`으로 재시도를 안내한다.
- 서버 시작 시 저장 profile은 복원하지만 현재 session, 후보, 두 mode와 자동 intent는
  복원하지 않는다.

current `sessionId`가 바뀌거나 없어지면 Dashboard는 이전 session의 AI 상세 응답을 즉시
숨긴다. 이전 turn이 늦게 완료돼도 새 session 화면에 다시 전달하지 않고, 이전 profile 장기
기억에도 저장하지 않는다.

## Voice Agent와 Assistant turn

하나의 Assistant `turnId`는 진행 안내 TTS, tool 호출과 최종 TTS를 하나의 논리적 turn으로
묶는다. 이 turn은 시작 시 원자적으로 캡처한 `sessionId`, `profileId`와 정책 상태에 귀속된다.

사용자 session 변경·종료는 이전 session을 먼저 무효화한 뒤 비동기 정리를 시작한다. Voice는
변경 event를 받으면 이전 session의 진행 중 Agent run, 실행되지 않은 부작용 tool, 재생 중·대기
중 TTS와 조건부 follow-up을 취소하고 해당 SDK 대화 session을 폐기한다. 취소와 경합해 늦게
도착한 transcript, tool 결과, audio와 Dashboard event는 같은 `turnId`라도 모두 버린다.

session 없음 또는 `MULTIPLE`·count 불일치 중 허용하는 일반 질문은 임시 비개인화 session을
사용한다. 이 실행은 기존 사용자 Agent session과 Mem0를 읽거나 쓰지 않으며 묶음 종료 후
폐기한다. 조건부 follow-up도 `request_followup` 요청이 있고 현재 정책을 재검증한 경우에만
연다.

## 명령과 동시성 계약

사용자에 종속된 명령은 Dashboard가 마지막으로 읽은 `expectedSessionId`를 전달하고,
`AutomationService`가 command lock 안에서 현재 session과 비교한다.

`CurrentUserSessionService`는 Voice가 turn 시작 시 사용할 원자적 불변 snapshot, 현재
`sessionId` 검증과 순서가 보장된 session 변경 event를 제공한다. 구체적인 event API나 SDK
타입은 이 task의 계약이 아니며 사용자 session service가 Agents SDK 객체를 소유하지 않는다.
물리 부작용 tool은 model이 호출을 생성한 시점이 아니라 `AutomationService` public API를
실제로 호출하기 직전에 캡처한 session을 재검증한다.

| 명령 | `expectedSessionId` | 이유 |
| --- | --- | --- |
| `AUTO`/`MANUAL` control mode 변경 | 필수 | 다른 session 제어 방식 변경 방지 |
| activity mode 변경 | 필수 | 현재 session과 profile 소유권 검증 |
| Agents SDK Desk function tool | 필수 | turn 시작 capture와 실제 실행 직전 재검증 |
| HOLD | 불필요 | 신원 독립 명시적 수동 명령 |
| 직접 높이 | 불필요 | 신원 독립 명시적 수동 명령 |
| STOP/CANCEL | 검사하지 않음 | 안전 명령 우선 |

동일 session ID라도 activity mode key와 profile 소유권은 서버에서 다시 조회한다. 여러 Dashboard,
background 자세 전이, 사용자 전환과 park는 같은 command lock과 generation으로 직렬화한다.

공개 오류 의미는 다음과 같다.

| HTTP | 의미 |
| ---: | --- |
| `404` | profile, activity mode 또는 enrollment 없음 |
| `409` | session 불일치, 현재 session 없음, 자동화 전제 불충족 또는 동시 등록 충돌 |
| `422` | schema, 범위 또는 입력값 오류 |
| `503` | camera, Vision, height, MQTT, relay 또는 profile 삭제에 필요한 memory 미준비 |

`409 SESSION_MISMATCH` 응답은 비밀 정보 없이 현재 snapshot을 다시 읽으라는 코드와 현재
session ID 또는 `null`을 제공한다. Dashboard는 성공처럼 표시하지 않고 snapshot을
갱신한다.

## 대표 결정표

| 사건 | 현재 session | control/activity mode | AUTO/park | 진행 이동 | Dashboard |
| --- | --- | --- | --- | --- | --- |
| 한 명·자세 3초, 얼굴 미확정 | 새 익명 | AUTO / 없음 | 2초 후 75/110 | 조건 유지 시 시작 | 게스트·기본 높이 |
| 한 명·자세 3초, A 확정 | 새 A 등록 | AUTO / 기본 | 2초 후 기본 mode 높이 | 조건 유지 시 시작 | A·기본 작업 모드 |
| A 얼굴만 가려짐, 단일 재실 지속 | A 유지 | 유지 | 계속 허용 | 유지 | A 유지 |
| 익명 중 A 얼굴 확정 | 새 A 등록 | AUTO/MANUAL 보존 / 기본 | AUTO면 즉시 기본 mode 목표 | 안전 목표 교체 | A로 전환 |
| A 중 B 얼굴 안정 확인 | 새 B 등록 | 새 AUTO / B 기본 | B 자세 재안정화 | A AUTO STOP | B로 전환 |
| A 중 고품질 미등록 얼굴 3초 | 새 익명 | 새 AUTO / 없음 | 기본 자세 재안정화 | A AUTO STOP | 게스트로 전환 |
| AUTO에서 작업 모드 변경 | 유지 | AUTO / 새 mode | fresh 자세로 새 목표 평가 | 필요 시 안전 교체 | 새 mode·LED |
| MANUAL에서 작업 모드 변경 | 유지 | MANUAL / 새 mode | 없음 | 이동 없음 | 새 mode·LED |
| `MULTIPLE`/count 불일치 | 기존 session 유지 | 유지 | BLOCKED | AUTO만 STOP | 자동 일시 중지 |
| 자세 또는 관련 frame stale | 기존 session 유지 | 유지 | BLOCKED | AUTO만 STOP | 원인 표시 |
| 안정 `VACANT` | 없음 | 없음 | PARK_WAITING | AUTO STOP | 사용자 없음 |
| fresh `VACANT` 30초 | 없음 | 없음 | 75cm PARK | 조건 유지 시 시작 | 주차 이동 표시 |
| park 중 사람 후보 | session 후보 | 없음 | park 취소 | PARK STOP | 사용자 확인 중 |
| session 없는 HOLD/직접 목표 | 없음 | 없음 | 자동화 없음 | 수동 허용 | 수동 상태 |
| 오래된 A session mode를 B에게 요청 | B 유지 | 유지 | 변경 없음 | 변경 없음 | `409` 후 새로고침 |
| 얼굴 등록 시작 | 없음 | 없음 | BLOCKED | AUTO STOP | 등록 진행 표시 |
| 서버 재시작·관측 없음 | 없음 | 없음 | WAITING_USER | STOP | 준비 중 |

## 후속 task 테스트 계약

- distinct frame과 monotonic fake clock으로 3초 재실·자세·미등록 얼굴 안정화를 재현한다.
- 상단 몸체 또는 얼굴과 하단 하체가 각각 단일 관측일 때만 결합 재실을 만든다.
- 얼굴 없이 익명 session이 생기고 2초 지연 뒤 자세에 따라 정확히 75/110cm 목표를 한 번만
  만든다.
- 최초 이동 대기 중 자세·재실·session·수동 명령과 frame freshness 변화가 목표를 취소한다.
- A 얼굴이 장시간 보이지 않아도 fresh 단일 재실이 이어지면 A session과 AUTO를 유지한다.
- 한 frame unknown 또는 낮은 품질은 A를 제거하지 않고, 고품질 unknown 3초는 새 익명
  session을 만든다.
- 익명 AUTO 이동 중 A가 확인되면 기존 generation을 무효화하고 등록 목표로 안전하게
  교체한다. 익명 MANUAL은 등록 후에도 MANUAL이다.
- A→B는 새 session ID, AUTO와 새 자세 안정화를 사용한다.
- 최초 목표 이후 등록·익명 자세 전환과 명시적 AUTO 재활성화는 모두 fresh 자세 5초를
  요구한다.
- `MULTIPLE`, count 불일치와 stale frame은 AUTO만 STOP하고 수동 명령은 허용한다.
- height·relay 오류는 AUTO와 수동을 차단하며 STOP은 항상 접수한다.
- 안정 VACANT 전에 session을 끝내지 않고 fresh VACANT 30초 전에는 park하지 않는다.
- park 중 사람 후보·수동 명령·장치 오류가 generation을 무효화하고 STOP한다.
- 서버 재시작 직후 stale VACANT와 retained height만으로 session이나 park 목표를 만들지 않는다.
- 이전 session ID의 control/activity mode·Voice tool을 `409`로 거절하고 HOLD·직접 높이·STOP은
  session 없이 처리한다.
- 얼굴 등록·재등록·삭제 중 이전 session과 identity 결과로 AUTO 이동하지 않는다.
- 익명 session은 activity mode와 profile 장기 기억을 읽거나 저장하지 않는다.
- 등록 session 시작·작업 모드 전환·수동 override·session 종료의 LED 적용 순서를 검증하고,
  WLED 실패가 작업 모드나 Desk 상태를 rollback하지 않는다.
- session 교대·종료 시 이전 AI 상세 응답을 즉시 숨기고 늦은 turn이 다시 나타나지 않는다.
- session 교대·종료는 이전 Agent run, 미실행 부작용 tool, TTS와 follow-up을 취소하고 SDK
  대화 session을 폐기한다.
- 진행 안내, tool과 최종 응답은 같은 `turnId`로 추적되고 취소 뒤 늦은 event는 모두 버린다.
- session 없음·다중 상태의 일반 질문은 임시 비개인화 session만 사용하며 기존 Agent
  대화와 Mem0에 접근하지 않는다.
- profile 삭제는 장기 기억까지 제거하며 memory 삭제 실패에서는 profile DB를 보존한다.

## 제외 범위와 알려진 제한

- 얼굴·자세 모델과 라이브러리 선정
- ROI 좌표, confidence, frame 수와 카메라 허용 시각 차이의 실측 보정
- 카메라 간 Re-ID, 장기 trajectory와 여러 책상 지원
- Pydantic 모델, SQLite migration, FastAPI route와 Dashboard 실제 구현
- Voice model·VAD·dependency, SDK history compaction과 Mem0 배포 세부
- 장애물 감지와 무인 park의 실물 안전 보장

무인 park는 fake 상태전이와 목표 기록 검증을 먼저 통과하고, 장애물·의자·장치 조건을 제한한
실물 검증 뒤 실제 relay 이동을 활성화한다.

## 완료 근거

- [x] 신원·재실·자세·session·control mode·activity mode·자동화를 별도 축으로 확정했다.
- [x] 등록·익명 session 시작, 유지, 전환과 종료 규칙을 확정했다.
- [x] 두 카메라 singleton 결합과 fail-closed 범위를 확정했다.
- [x] AUTO와 수동 제어의 Vision·장치 차단 범위를 분리했다.
- [x] `expectedSessionId`, 오류 의미와 STOP 우선순위를 확정했다.
- [x] 얼굴 lifecycle, 서버 재시작과 30초 park 순서를 확정했다.
- [x] Voice의 사용자·Agent·audio session 수명과 교대 시 취소 경계를 확정했다.
- [x] 대표 시나리오 결정표와 후속 자동 테스트 계약을 기록했다.
- [x] workflow와 API 계약 문서에 같은 정책을 반영했다.
