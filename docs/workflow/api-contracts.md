# 워크플로우 API 계약

아래는 목표 계약이다. 현재 `/api/status`, `/api/control`, `/api/target`, `/api/profiles`와
WLED API는 구현돼 있지만 사용자·Vision·자동화·preset 계약은 아직 없다.

## 공통 규칙

- JSON 필드는 기존 API처럼 camelCase를 사용하고 unknown field를 거부한다.
- 신원, 재실, 자세, 사용자 session과 자동화 상태를 한 status enum으로 합치지 않는다.
- timestamp는 UTC wall clock을 사용한다. 내부 monotonic 값은 반환하지 않는다.
- 현재 사용자를 변경하는 PUT/DELETE API는 제공하지 않는다.
- 사용자 종속 명령은 `expectedSessionId`를 받고 command lock 안에서 비교한다.
- STOP과 목표 CANCEL은 session 검증보다 먼저 처리한다.

## 현재 사용자

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/current-user` | 서버가 결정한 현재 등록·익명 session read-only 조회 |

익명 session 예시:

```json
{
  "session": {
    "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
    "kind": "ANONYMOUS",
    "profileId": null,
    "startedAt": "2026-08-16T10:00:03Z",
    "changedAt": "2026-08-16T10:00:03Z"
  }
}
```

등록 session은 `kind="REGISTERED"`와 `profileId`를 제공한다. 현재 session이 없으면
`{"session": null}`을 반환한다. mode, 자세와 재실을 이 객체에 섞지 않는다.

## Vision

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/vision/status` | 카메라, 신원·재실·자세와 결합 freshness 조회 |

최소 구조 예시:

```json
{
  "cameras": {
    "upper": {"status": "ONLINE", "observedAt": "...", "expiresAt": "..."},
    "lower": {"status": "ONLINE", "observedAt": "...", "expiresAt": "..."}
  },
  "identity": {
    "status": "NO_FACE",
    "profileId": null,
    "observedAt": "...",
    "expiresAt": "..."
  },
  "presence": {
    "status": "PRESENT_SINGLE",
    "upperCount": 1,
    "lowerCount": 1,
    "observedAt": "...",
    "expiresAt": "..."
  },
  "posture": {
    "status": "STANDING",
    "candidateSince": "...",
    "observedAt": "...",
    "expiresAt": "..."
  },
  "association": {
    "usable": true,
    "reasonCodes": []
  }
}
```

신원 status는 `MATCHED`, `UNKNOWN_FACE`, `AMBIGUOUS`, `NO_FACE`, `UNKNOWN`, 재실은
`PRESENT_SINGLE`, `VACANT`, `MULTIPLE`, `UNKNOWN`, 자세는 `SITTING`, `STANDING`,
`UNKNOWN`을 사용한다. raw confidence와 내부 얼굴 threshold는 일반 Dashboard 응답에서
제외한다.

## 얼굴 등록

| Method | 경로 | 목적 |
| --- | --- | --- |
| `POST` | `/api/profiles/{id}/face-enrollments` | 등록 session 시작 |
| `GET` | `/api/face-enrollments/{id}` | 진행 상태 조회 |
| `DELETE` | `/api/face-enrollments/{id}` | 진행 중 등록 취소 |
| `DELETE` | `/api/profiles/{id}/face` | 저장된 얼굴 등록 제거 |

시작은 `202 Accepted`, 상태 조회는 `200`이다. 다른 등록 진행 중이면 `409`, profile 없음은
`404`, camera/Vision 미준비는 `503`이다.

등록·재등록·삭제 시작은 진행 AUTO를 STOP하고 현재 session을 종료한다. 성공·실패·취소 후
일반 background 재실·식별을 새로 통과하기 전에는 current user를 만들지 않는다.

## 자동화 상태

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/automation/status` | session 연결, mode, 자동화와 park 상태 조회 |

익명 AUTO 예시:

```json
{
  "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
  "mode": "AUTO",
  "state": "OBSERVING",
  "heightPolicy": "ANONYMOUS_DEFAULT",
  "postureCandidate": "STANDING",
  "candidateSince": "2026-08-16T10:00:00Z",
  "targetHeightCm": null,
  "intentSource": null,
  "blockedReasonCodes": [],
  "initialMoveDueAt": "2026-08-16T10:00:05Z",
  "parkDueAt": null,
  "updatedAt": "2026-08-16T10:00:03Z"
}
```

mode가 없으면 `null`이다. state는 `WAITING_USER`, `OBSERVING`, `READY`, `MOVING`,
`MANUAL`, `BLOCKED`, `PARK_WAITING`, `PARKING`을 사용한다. `intentSource`는 최소한
`REGISTERED_POSTURE`, `ANONYMOUS_POSTURE`, `PARK`, `MANUAL`을 구분한다.

## mode 변경

| Method | 경로 | 목적 |
| --- | --- | --- |
| `PUT` | `/api/desk/mode` | 현재 session의 `AUTO`/`MANUAL` 변경 |
| `PUT` | `/api/profiles/{id}/automation` | profile 자세 유지 시간 변경 |

요청 예시:

```json
{
  "mode": "AUTO",
  "expectedSessionId": "session-550e8400-e29b-41d4-a716-446655440000"
}
```

현재 session 없음, 불일치 또는 AUTO 재개 조건 불충족은 `409`다. AUTO 요청은 기존 이동
STOP, 자동 generation과 자세 후보 초기화 후 성공하며 fresh 자세를 다시 안정화한다.

## 사용자 preset 설정

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/profiles/{id}/presets` | 설정 화면용 custom preset 목록 |
| `POST` | `/api/profiles/{id}/presets` | 이름·높이 preset 생성 |
| `PATCH` | `/api/presets/{presetId}` | 이름 또는 높이 수정 |
| `DELETE` | `/api/presets/{presetId}` | preset 삭제 |

이 API는 profile 설정용이며 호출해도 현재 session, mode와 책상을 바꾸지 않는다.

## 현재 session preset

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/desk/presets` | 현재 session의 자세·custom preset 합성 목록 |
| `POST` | `/api/desk/presets/{presetKey}/apply` | MANUAL 전환 후 목표 접수 |

조회 응답은 현재 `sessionId`, `heightPolicy`와 항목을 함께 반환한다. 등록 session은 profile
자세 높이와 custom preset, 익명 session은 기본 75/110cm 자세 항목만 반환한다. session이
없으면 `sessionId=null`, 빈 목록을 `200`으로 반환한다.

적용 요청 예시:

```json
{
  "expectedSessionId": "session-550e8400-e29b-41d4-a716-446655440000"
}
```

합성 항목은 `key`, `kind`, `name`, `heightCm`, `editable`, `heightPolicy`를 가진다. 자세 key는
등록·익명 모두 `posture:sitting`, `posture:standing`을 사용하고 서버가 현재 session의 높이
정책으로 해석한다.

적용 성공 `200`은 목표 접수 의미다. 현재 session 없음·불일치, 다른 사용자 custom preset과
AUTO 차단 상태는 `409`, 높이·relay 미준비는 `503`이다.

## 기존 책상 명령

현재 `/api/control`과 `/api/target`은 Dashboard가 `DeskController`에 직접 위임한다. 목표
구조에서는 `AutomationService` 경계를 거치지만 HOLD, 직접 목표와 STOP은 신원 독립
명령으로 유지한다.

| 명령 | session 요구 | mode 결과 |
| --- | --- | --- |
| HOLD | 없음 | session이 있으면 MANUAL, 없으면 mode 없음 |
| 직접 목표 SET | 없음 | session이 있으면 MANUAL, 없으면 mode 없음 |
| 사용자 STOP | 없음 | session이 있으면 MANUAL, 없으면 mode 없음 |
| 목표 CANCEL | 없음 | STOP과 동일한 우선순위 |

따라서 기존 request에 `expectedSessionId`를 필수 추가하지 않는다. wire MQTT 계약과
`DeskController` 안전 검증도 바꾸지 않는다.

## session 충돌과 오류 응답

사용자 종속 명령은 `expectedSessionId`가 현재 값과 다르면 처리 전에 `409`로 거절한다.

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

Dashboard는 성공 상태나 낙관적 높이를 표시하지 않고 current-user, automation과 preset
snapshot을 다시 읽는다.

| HTTP | 공개 의미 |
| ---: | --- |
| `404` | profile, preset 또는 enrollment 없음 |
| `409` | session 불일치·없음, 전환 중, 자동화 전제 불충족, 동시 등록 충돌 |
| `422` | unknown field, type, 범위와 schema 오류 |
| `503` | camera, Vision, height, MQTT 또는 relay 미준비 |

STOP/CANCEL은 stale session, current user 없음과 Vision 차단을 이유로 거절하지 않는다.
