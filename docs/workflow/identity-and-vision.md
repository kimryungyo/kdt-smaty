# 사용자 식별과 Vision 워크플로우

현재 session은 Dashboard 선택이 아니라 서버의 background Vision으로만 결정한다. 얼굴이
등록되지 않았거나 보이지 않아도 단일 재실이 안정화되면 익명 session을 만들며,
`CurrentUserSnapshot`은 Dashboard, 자동화와 Voice가 읽는 단일 서버 사용자 문맥이다.

## 시작과 background 처리

```text
WebRtcCameraPublisher.start() → MediaMTX WHIP
  → WebRtcFrameSource.start() → MediaMTX WHEP
  → frame 전처리
  → PresenceDetector / shared FaceDetector / PostureDetector
  → fresh face box를 FaceRecognizer가 재사용
  → VisionService
  → CurrentUserSessionService
```

처리가 입력 FPS보다 느려도 과거 frame queue를 쌓지 않고 최신 frame 하나만 처리한다. 같은
frame을 여러 관측으로 세지 않고, 이전 결합 관측 뒤 상단·하단이 모두 distinct frame을 낸
pair에서만 안정화 timer를 전진한다. 오래된 frame은 새 관측으로 사용하지 않으며 무거운
추론은 event loop 밖에서 수행한다. Task 04의 기본 Noop detector는 model 미구성 상태를
`MODEL_UNAVAILABLE`로 fail-closed하며, 실제 model·threshold와 preview 실측은 별도
camera 검증이 필요하다.

## 분리된 공개 상태

신원, 재실, 자세와 session을 하나의 status로 합치지 않는다.

| 축 | 상태 | 의미 |
| --- | --- | --- |
| 신원 | `MATCHED` | 등록 profile과 안정적으로 일치 |
| 신원 | `UNKNOWN_FACE` | 고품질 얼굴이 등록 template과 불일치 |
| 신원 | `AMBIGUOUS` | 후보 간 차이가 부족하거나 불확실 |
| 신원 | `NO_FACE` | fresh frame에 얼굴이 보이지 않음 |
| 신원 | `UNKNOWN` | frame/model 오류 또는 유효 관측 없음 |
| 재실 | `PRESENT_SINGLE` | 상단 카메라 전체 화각에서 한 명 |
| 재실 | `VACANT` | 이탈이 안정화됨 |
| 재실 | `MULTIPLE` | 상단 카메라 전체 화각에 두 명 이상 |
| 재실 | `UNKNOWN` | 상단 stale·모델 오류 또는 유효 count 없음 |
| 자세 | `SITTING`, `STANDING`, `UNKNOWN` | 재실과 독립적인 자세 관측 |

각 카메라 frame, 신원, 재실과 자세는 자체 `observedAt`과 `expiresAt` 또는 age를 가진다.
내부 duration에는 monotonic clock, API 시각에는 UTC wall clock을 사용한다.

### 재실·자세 안정화

재실은 현재 stable 값과 다른 관측이 시작되면 3초 관측 묶음을 열고, 서로 다른 결합 frame
최소 3개 중 70% 이상을 차지한 값을 다음 stable 값으로 채택한다. 표결 중에는 기존 stable
값을 유지한다. 상단 `MULTIPLE`도 이 규칙을 따른다.

하단 자세는 시연 UX를 위해 시간 대신 최근 distinct sample 수를 쓴다. `SITTING↔STANDING`은
최근 3개 중 현재 자세가 2개일 때 전이한다. stable 자세에서 raw `UNKNOWN`은 최근 6개가 모두
`UNKNOWN`일 때만 stable `UNKNOWN`이 된다. `UNKNOWN`에서 정상 자세로 돌아올 때는 같은 정상
자세 2개가 연속으로 들어와야 복구한다.
따라서 단발 `UNKNOWN`, 자세 오탐은 raw 상태에만 보이고 session과 AUTO를 흔들지 않는다.

카메라 단절과 frame/result stale은 실제 입력이 멈춘 파이프라인 장애이므로 다수결을 기다리지
않고 즉시 stable 값을 `UNKNOWN`으로 만들고 AUTO를 차단한다. 단일 detector 예외나 모델
결과 누락은 해당 결합 frame의 `UNKNOWN` 표로 처리해 순간 오류를 흡수하고, 하단 자세에서는
최근 6 sample이 모두 `UNKNOWN`일 때만 stable 값을 `UNKNOWN`으로 바꾼다. 새 distinct 상·하단 frame이 다시
들어온 뒤에는 처음부터 3초를 재확인한다.
운영 장비의 상·하단 직렬 추론 한 묶음은 약 0.8초이므로 frame/result 만료값은 3초로 둔다.
이는 정상 처리 지연을 stale로 오판하지 않으면서 실제 입력 정지는 3초 안에 차단한다.

## 상단 재실과 하단 자세 결합

첫 구현은 Re-ID 없이 단일 책상 singleton만 결합한다.

```text
상단 전체 화각의 person count 한 명
  + 하단 전체 화각에서 confidence가 가장 높은 pose 한 명
  + 각 fresh frame과 허용 시각 차이
  → PRESENT_SINGLE 후보
```

상단 얼굴은 신원 식별 전용이며 person count에 더하지 않는다. 상단에서 두 명 이상이 3초
안정화 창의 다수결을 통과해 stable `MULTIPLE`이 되면 진행 AUTO를 STOP하고 새 AUTO를
차단한다. 하단은 여러 pose가 있어도 최고 confidence 한 명의 자세만 사용하며, 하단 count나 상단과의 count 불일치는 AUTO 차단
근거가 아니다.

현재 상단 재실 count는 YOLO pose 모델의 `person` 검출 수로 만들고, YuNet은 얼굴 box와
profile 식별에만 사용한다. 따라서 서 있는 사용자의 얼굴이 상단 frame 밖에 있어도 상체가
사람으로 검출되면 익명 재실·AUTO 후보가 될 수 있다. 반대로 얼굴 box만으로 재실 count를
올리지 않는다.

YuNet이 화면 경계에 걸린 얼굴의 box·landmark를 frame 밖 좌표로 반환하면 해당 얼굴 후보만
버린다. 이 경우는 신원 확인에는 쓰지 않지만, 독립 YOLO 상체 재실 판정과 자세 자동화는
중단하지 않는다. 행 폭·NaN 같은 구조적 모델 출력 오류만 detector 오류로 처리한다.

## 사용자 session

session snapshot은 최소한 다음 필드를 가진다.

| 필드 | 의미 |
| --- | --- |
| `sessionId` | 매 session마다 새로 발급한 예측 불가능한 ID |
| `kind` | `REGISTERED` 또는 `ANONYMOUS` |
| `profileId` | 등록 session의 profile 또는 익명 session의 `null` |
| `startedAt` | session 시작 시각 |
| `changedAt` | 마지막 session 변경 시각 |

session이 없으면 위 객체 전체가 `null`이다. control/activity mode와 자동화 상태는 별도
`AutomationSnapshot`이 같은 session ID에 연결한다.

### session 시작

상단 person count 한 명과 하단 최고 score 자세가 각각 안정화되면 session을 만든다.

- 등록 얼굴도 안정화됐으면 `REGISTERED` session
- 등록 사용자가 확정되지 않았으면 `ANONYMOUS` session
- 한 frame 얼굴 후보만으로 등록 session을 만들지 않음
- 익명 session을 위해 가짜 profile이나 공용 얼굴 row를 만들지 않음

얼굴이 전혀 보이지 않아도 익명 session을 시작할 수 있다. 등록·익명 session은 모두
`controlMode=AUTO`로 시작한다. 등록은 profile의 기본 작업 모드와 LED를 적용하고 익명은
activity mode 없이 75/110cm 높이 정책을 사용한다. 모든 자동 목표는 stable 자세가 된 뒤
2초 동안 조건이 유지돼야 한다.

### 얼굴이 보이지 않을 때

fresh한 `PRESENT_SINGLE` 연속성이 유지되면 얼굴이 계속 보이지 않아도 기존 등록 또는 익명
session을 유지한다. 얼굴 재확인 timeout은 두지 않는다. `NO_FACE`, 낮은 품질과 한 frame
false negative는 사용자 변경 근거가 아니다.

### 사용자 전환

- 익명 중 A가 안정적으로 식별되면 새 등록 session ID와 A의 기본 작업 모드를 적용한다. 익명
  AUTO이면 현재 자세로 기본 mode 목표를 안전하게 교체하고, 익명 MANUAL이면 MANUAL을 보존하고 LED만 적용한다.
- A 중 B가 안정적으로 식별되면 A AUTO를 STOP하고 A session을 종료한 뒤 B 새 AUTO
  session과 자세 안정화를 시작한다.
- A 중 고품질 `UNKNOWN_FACE`가 3초 안정화되면 A AUTO와 session을 종료하고 새 익명 AUTO
  session을 시작한다. A와 미등록 얼굴이 동시에 보이면 `MULTIPLE`로 처리한다.
- 상단의 `MULTIPLE`과 관측 불확실성은 session을 유지하지만 AUTO를
  STOP·차단한다. 단발 추론 오탐은 위 3초 다수결에서 흡수한다.
  등록 session은 같은 얼굴 재확인, 익명 session은 단일 재실 3초 재안정화 뒤 AUTO 차단을
  해제한다.

### session 종료

안정화된 `VACANT`, 다른 사용자 전환, 얼굴 등록·재등록·삭제 시작, 활성 profile 삭제와
서버 종료·재시작에서 session을 종료한다. 종료·교대 시 WLED OFF를 best-effort로 요청하되
실패가 STOP과 session 전이를 rollback하지 않는다. 서버 시작 시 저장 profile은 남지만 과거
session, 후보와 두 mode는 복원하지 않는다.

## 얼굴 식별

```text
최신 상단 frame
  → shared FaceDetector의 fresh box와 얼굴 수 검사
  → 얼굴 정렬·품질 검사
  → FaceEmbeddingExtractor(executor)
  → 등록 embedding 비교
  → raw identity observation
  → IdentityStateService 안정화
  → CurrentUserSessionService 전이
```

얼굴 detector는 재실 관측과 신원 식별이 같은 최신 결과를 공유하며 두 loop에서 중복 실행하지
않는다. `FaceEmbeddingExtractor` model도 애플리케이션 시작 시 한 번 load하고 얼굴 등록과
식별이 같은 인스턴스를 사용한다. model이 동시 호출을 지원하지 않으면 작은 내부 lock으로
직렬화한다.

## 얼굴 등록

```text
Dashboard 등록 시작
  → profile 존재와 fresh camera 확인
  → 동시 등록 충돌 확인
  → 진행 AUTO STOP과 현재 session 종료
  → enrollment ID 반환

background enrollment
  → 한 명의 얼굴 확인
  → 서로 다른 시점의 품질 표본 수집
  → 임베딩 추출과 표본 일관성 검사
  → 유효 embedding 3~5개를 profile ID에 개별 row로 원자 저장
```

등록 상태는 다음과 같다.

```text
IDLE → WAITING_FACE → CAPTURING → PROCESSING → SUCCEEDED
                  └──────────────→ CANCELLED / FAILED
```

snapshot에는 enrollment/profile ID, 상태, 필요·채택 표본 수, 시각과 `failureCode`만 노출한다.
얼굴 이미지와 embedding vector는 반환하지 않는다. 평균 vector 하나로 합치지 않으며 재등록은
새 3~5개 집합이 완성된 뒤 기존 집합을 transaction으로 교체한다. 등록 중 재실·자세는 계속 관측하지만 새
identity와 AUTO는 발행하지 않는다.

등록 성공·실패·취소 자체는 현재 사용자를 설정하거나 이전 session을 복원하지 않는다. 일반
background 식별과 재실 안정화를 새로 통과해야 다음 session이 생긴다.

## 자동화 입력과 Voice

AUTO에는 현재 session, 상단의 fresh `PRESENT_SINGLE`, 하단 최고 score의 fresh 자세와 안전한 장치 상태가
모두 필요하다. 상단 다중·자세 또는 frame 만료는 AUTO만 차단하며 명시적 수동
제어는 허용한다.

등록 session은 `profile:<profile_id>` 기억을 사용할 수 있다. 익명 session은 일반 Voice와
session 범위의 짧은 history만 사용하고 profile 장기 기억을 읽거나 저장하지 않는다.
상단 `MULTIPLE`에서는 등록 사용자 개인화를 일시 차단한다. Dashboard에서 편집한
profile을 Voice 사용자로 사용하지 않는다.

`CurrentUserSessionService`는 Voice가 turn 시작 상태를 원자적으로 capture하고 실행 직전에
`sessionId`를 검증할 수 있는 snapshot·검증 API와 순서가 보장된 변경 event를 제공한다.
교대·종료 event는 이전 Agent run·TTS·follow-up 취소와 SDK 대화 session 폐기의 근거다.
session 없음·다중 상태의 일반 질문은 별도 임시 비개인화 session을 사용하며 기존 사용자
대화나 Mem0를 읽고 쓰지 않는다.
