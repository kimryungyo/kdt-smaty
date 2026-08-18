# 워크플로우 API 계약

아래는 현재 구현된 API 계약과 남은 production 제한을 함께 기록한다. `/api/current-user`,
`/api/vision/status`, 자동화·작업 모드와 `/api/assistant/latest`까지 구현·자동 검증되어 있다.
production 얼굴 추론, 실제 camera 및 hardware/Voice live 검증은 이 API 구현 완료와 별개다.

## 공통 규칙

- JSON 필드는 기존 API처럼 camelCase를 사용하고 unknown field를 거부한다.
- 신원, 재실, 자세, 사용자 session, `controlMode`, `activityMode`와 자동화 상태를 서로 다른
  필드로 유지한다.
- timestamp는 UTC wall clock을 사용한다. 내부 monotonic 값은 반환하지 않는다.
- 현재 사용자를 변경하는 PUT/DELETE API는 제공하지 않는다.
- 사용자 종속 명령은 `expectedSessionId`를 받고 command lock 안에서 비교한다.
- STOP과 목표 CANCEL은 session·Vision·전역 readiness 검사보다 먼저 처리한다.
- 전역 `/health/ready`를 profile CRUD와 상태 조회의 일괄 차단 조건으로 사용하지 않는다.

## 현재 사용자

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/current-user` | 서버가 결정한 현재 등록·익명 session read-only 조회 |

```json
{
  "session": {
    "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
    "kind": "REGISTERED",
    "profileId": "profile-a",
    "startedAt": "2026-08-16T10:00:03Z",
    "changedAt": "2026-08-16T10:00:03Z"
  }
}
```

익명은 `kind="ANONYMOUS"`, `profileId=null`이다. session이 없으면 `{"session":null}`이다.
제어 방식, 작업 모드, 자세와 재실은 이 객체에 섞지 않는다.

## Vision

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/vision/status` | 카메라, 신원·재실·자세와 결합 freshness 조회 |

```json
{
  "cameras": {
    "upper": {"status":"ONLINE","observedAt":"...","expiresAt":"..."},
    "lower": {"status":"ONLINE","observedAt":"...","expiresAt":"..."}
  },
  "identity": {"status":"MATCHED","profileId":"profile-a","observedAt":"...","expiresAt":"..."},
  "presence": {"status":"PRESENT_SINGLE","upperCount":1,"lowerCount":1,"observedAt":"...","expiresAt":"..."},
  "posture": {"status":"SITTING","candidateSince":"...","observedAt":"...","expiresAt":"..."},
  "association": {"usable":true,"reasonCodes":[]}
}
```

Task 04는 identity 판정을 아직 수행하지 않으므로 같은 shape의
`{"status":"UNKNOWN","profileId":null,"observedAt":null,"expiresAt":null}`를 반환한다.
Task 05가 이 공개 shape의 실제 identity 값을 채운다. raw frame, 얼굴 box, crop, embedding
vector와 detector 내부 threshold는 이 endpoint에 노출하지 않는다.

신원은 `MATCHED`, `UNKNOWN_FACE`, `AMBIGUOUS`, `NO_FACE`, `UNKNOWN`, 재실은
`PRESENT_SINGLE`, `VACANT`, `MULTIPLE`, `UNKNOWN`, 자세는 `SITTING`, `STANDING`,
`UNKNOWN`을 사용한다. raw confidence와 얼굴 threshold는 일반 응답에서 제외한다.

## 얼굴 등록

| Method | 경로 | 목적 |
| --- | --- | --- |
| `POST` | `/api/profiles/{id}/face-enrollments` | 등록 session 시작 |
| `GET` | `/api/face-enrollments/{id}` | 진행 상태 조회 |
| `DELETE` | `/api/face-enrollments/{id}` | 진행 중 등록 취소 |
| `DELETE` | `/api/profiles/{id}/face` | 저장된 얼굴 표본 전체 제거 |

등록은 서로 다른 시점의 유효 embedding 3~5개를 profile에 원자적으로 저장한다. API는 얼굴
이미지, vector, similarity와 threshold를 반환하지 않는다. 시작은 `202`, 다른 등록 진행은
`409`, profile 없음은 `404`, camera/Vision 미준비는 `503`이다.

등록·재등록·삭제 시작은 진행 AUTO를 STOP하고 현재 session을 종료한다. 완료 뒤에도 일반
background 재실·식별을 새로 통과해야 한다.

## profile 작업 모드 설정

profile의 기존 `sittingHeightCm`, `standingHeightCm`, `ledColor`는 내장 `기본` 작업 모드다.
기본값 수정은 기존 profile PATCH를 사용한다.

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/profiles/{id}/activity-modes` | 기본+custom 작업 모드 합성 목록 |
| `POST` | `/api/profiles/{id}/activity-modes` | custom 작업 모드 생성 |
| `PATCH` | `/api/activity-modes/{modeId}` | custom 이름·높이·LED 수정 |
| `DELETE` | `/api/activity-modes/{modeId}` | custom 작업 모드 삭제 |

생성 요청 예시:

```json
{
  "name": "독서",
  "sittingHeightCm": 82.0,
  "standingHeightCm": 108.0,
  "ledColor": "FFD080"
}
```

합성 항목은 `key`, `kind`, `name`, `sittingHeightCm`, `standingHeightCm`, `ledColor`,
`editable`을 가진다. 기본 항목은 `key="default"`, `kind="DEFAULT"`, `editable=false`다.
설정 CRUD는 current session, active mode, WLED와 Desk를 바꾸지 않는다. 현재 active custom
mode 삭제 `409` guard는 Task 06의 active snapshot 구현과 함께 연결한다. 이 Task의 삭제는
custom row가 존재하면 `204`, 없으면 `404`이며, 수정값은 다음 mode 선택 또는 다음 session부터
적용된다. 같은 profile의 정규화 이름 중복은 `409`, unknown field·빈 이름·높이/LED 범위 오류는
`422`다.

## 자동화 상태

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/automation/status` | session 연결, 제어 방식, 작업 모드, 자동화와 park 상태 조회 |

등록 AUTO 예시:

```json
{
  "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
  "controlMode": "AUTO",
  "activityMode": {
    "key": "mode-reading",
    "kind": "CUSTOM",
    "name": "독서",
    "sittingHeightCm": 82.0,
    "standingHeightCm": 108.0,
    "ledColor": "FFD080"
  },
  "state": "OBSERVING",
  "heightPolicy": "PROFILE_ACTIVITY_MODE",
  "postureCandidate": "SITTING",
  "candidateSince": "2026-08-16T10:00:00Z",
  "targetHeightCm": null,
  "intentSource": null,
  "blockedReasonCodes": [],
  "initialMoveDueAt": null,
  "parkDueAt": null,
  "updatedAt": "2026-08-16T10:00:03Z"
}
```

익명 session은 `activityMode=null`, `heightPolicy="ANONYMOUS_DEFAULT"`다. session이 없으면
`controlMode`, `activityMode`와 `sessionId`가 `null`이다. state는 `WAITING_USER`,
`OBSERVING`, `READY`, `MOVING`, `MANUAL`, `BLOCKED`, `PARK_WAITING`, `PARKING`을 사용한다.

## 제어 방식 변경

| Method | 경로 | 목적 |
| --- | --- | --- |
| `PUT` | `/api/desk/control-mode` | 현재 session의 `AUTO`/`MANUAL` 변경 |

```json
{
  "controlMode": "AUTO",
  "expectedSessionId": "session-550e8400-e29b-41d4-a716-446655440000"
}
```

현재 session 없음·불일치는 `409`다. AUTO 요청은 기존 이동 STOP, generation과 자세 후보
초기화 후 active 작업 모드로 fresh 자세를 2초 다시 확인한다.

## 현재 session 작업 모드 변경

| Method | 경로 | 목적 |
| --- | --- | --- |
| `PUT` | `/api/desk/activity-mode` | 등록 session의 active 작업 모드 선택 |

```json
{
  "activityModeKey": "mode-reading",
  "expectedSessionId": "session-550e8400-e29b-41d4-a716-446655440000"
}
```

서버가 현재 profile의 mode 소유권과 저장값을 다시 조회한다. 성공은 active snapshot과 LED 적용
시도가 접수됐다는 뜻이다.

- AUTO: control mode 유지, 이전 generation 무효화 후 fresh·안정된 현재 자세로 새 높이 평가
- MANUAL: control mode 유지, active mode와 LED만 변경하고 책상은 움직이지 않음
- Vision 불확실: active mode와 LED는 변경하고 이동만 `BLOCKED`
- WLED 실패: mode 변경은 유지하고 WLED 상태만 degraded

session 없음·익명·불일치·다른 profile mode는 `409`, mode 없음은 `404`다.

## 기존 책상 명령

`/api/control`과 `/api/target`은 `AutomationService`를 거치되 신원 독립 명령으로 유지한다.

| 명령 | session 요구 | 결과 |
| --- | --- | --- |
| HOLD | 없음 | session이 있으면 MANUAL, active activity mode 유지 |
| 직접 목표 SET | 없음 | session이 있으면 MANUAL, active activity mode 유지 |
| 사용자 STOP | 없음 | session이 있으면 MANUAL, active activity mode 유지 |
| 목표 CANCEL | 없음 | STOP과 동일한 우선순위 |

기존 request에 `expectedSessionId`를 필수 추가하지 않는다. wire MQTT 계약과
`DeskController` 안전 검증도 바꾸지 않는다.

## WLED 수동 제어

기존 `/api/wled/control` 동작은 유지한다. Dashboard가 현재 session 안에서 호출할 때는 읽은
`expectedSessionId`를 선택적으로 함께 보내 session override로 귀속한다. session이 없으면
전역 수동 제어다.

session override는 저장된 작업 모드의 `ledColor`를 수정하지 않는다. 다음 작업 모드 전환이나
다음 session 시작에서 제거되고 저장 색상이 다시 적용된다. session 종료 시 OFF를 best-effort로
요청한다. WLED 미연결은 `503`이지만 Desk와 mode 상태는 rollback하지 않는다.

## Assistant 최신 turn

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/assistant/latest` | 현재 session에 표시할 최신 Assistant turn 하나 조회 |

초기 구현은 polling만 사용한다. SSE, WebSocket, 전체 대화 이력 API를 함께 만들지 않는다.

```json
{
  "turn": {
    "turnId": "turn-...",
    "sessionId": "session-...",
    "phase": "FINAL",
    "sequence": 4,
    "status": "SUCCEEDED",
    "title": "내일 날씨",
    "summary": "...",
    "detail": "...",
    "updatedAt": "..."
  }
}
```

표시할 turn이 없으면 `{"turn":null}`이다. 서버는 current session과 다른 turn을 반환하지
않고, Dashboard도 응답의 `sessionId`, `turnId`, `sequence`를 다시 확인한다.

## session 충돌과 오류 응답

사용자 종속 명령의 `expectedSessionId`가 현재 값과 다르면 처리 전에 `409`로 거절한다.

```json
{
  "detail": {
    "code": "SESSION_MISMATCH",
    "message": "현재 사용자 세션이 변경되었습니다.",
    "currentSessionId": "session-new-or-null",
    "refresh": true
  }
}
```

Dashboard는 낙관적 성공을 표시하지 않고 current-user와 automation snapshot을 다시 읽는다.

## Voice 상태

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/voice/status` | Voice hardware/runtime의 content-free 상태 조회 |

```json
{
  "state": "WAITING_WAKE",
  "lastTransitionAt": "2026-08-16T10:00:03Z",
  "followupExpiresAt": null,
  "lastError": null
}
```

Voice가 비활성화되어 container에 없으면 정상 상태로
`{"state":"DISABLED","lastTransitionAt":null,"followupExpiresAt":null,"lastError":null}`를
반환한다. 활성 Voice의 timestamp는 UTC ISO 시각이다. transcript, audio, provider 세부와
비밀값은 이 API에 포함하지 않는다. 이 상태 조회에는 전역 readiness guard를 적용하지 않는다.

| HTTP | 공개 의미 |
| ---: | --- |
| `404` | profile, activity mode 또는 enrollment 없음 |
| `409` | session 불일치·없음, active mode 삭제, 자동화 전제 불충족, 동시 등록 충돌 |
| `422` | unknown field, type, 범위와 schema 오류 |
| `502` | WLED 등 외부 장치의 잘못된 응답 |
| `503` | 해당 명령에 필요한 camera, Vision, height, MQTT, relay, WLED 또는 storage 미준비 |

STOP/CANCEL은 stale session, current user 없음, Vision 차단과 전역 readiness를 이유로
거절하지 않는다.
