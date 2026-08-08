# Dashboard HTTP API

React는 같은 origin의 상대 `/api/...` 경로만 사용한다. 명령 성공은 controller가
명령을 접수했다는 의미이며, 실제 이동 완료는 뒤이은 `GET /api/status`에서 확인한다.

## 상태와 제어

`GET /api/status`는 `200`으로 Desk 상태를 반환한다. 모든 필드는 camelCase이며 시각은
timezone-aware ISO 8601이다.

```json
{
  "state": "IDLE",
  "height": {"heightCm": 90.0, "observedAt": "2026-08-08T00:00:00Z", "status": "ONLINE"},
  "relay": {"event": "online", "state": "STOP", "firmware": "smartdesk-fin-relay-1.0.0", "code": null, "detail": null, "receivedAt": "2026-08-08T00:00:00Z", "lastError": null},
  "targetHeightCm": null,
  "direction": null,
  "detail": "안전 정지 상태입니다.",
  "lastError": null,
  "updatedAt": "2026-08-08T00:00:00Z"
}
```

`POST /api/control`은 아래 body를 받고 같은 상태 snapshot을 `200`으로 반환한다.

```json
{"action":"HOLD","direction":"UP"}
```

```json
{"action":"STOP"}
```

`POST /api/target`도 같은 응답을 사용한다.

```json
{"action":"SET","targetCm":90.0}
```

```json
{"action":"CANCEL"}
```

`HOLD`는 누르는 동안 약 200ms마다 갱신한다. release, cancel, blur, 숨김, pagehide와
unmount는 best-effort `STOP`을 보내며, 이 요청도 유실되면 `DeskController` watchdog이
최종 STOP을 수행한다.

## 프로필

`GET /api/profiles`는 profile 배열을 `200`으로 반환한다. `GET /api/profiles/{id}`는 한
항목을 반환한다. `POST /api/profiles`는 `201`, `PATCH /api/profiles/{id}`는 `200`,
`DELETE /api/profiles/{id}`는 body 없는 `204`이다.

```json
{
  "id": "profile-0123456789abcdef0123456789abcdef",
  "name": "홍길동",
  "sittingHeightCm": 80.0,
  "standingHeightCm": 105.0,
  "ledColor": "FF3000"
}
```

생성에는 `id`를 보내지 않으며, PATCH는 전달한 field만 바꾼다. `ledColor: null`은 LED
색상을 제거한다. 높이는 75~115cm이고 unknown field와 유효하지 않은 값은 거부한다.

모든 route는 요청 검증 오류를 `422`, 없는 profile을 `404`, profile ID 충돌 또는 현재
Desk 안전 상태에서 거부된 명령을 `409`, controller·SQLite가 준비되지 않았거나 storage
오류인 경우를 `503`으로 반환한다. 예상하지 못한 오류는 FastAPI 기본 `500`으로 남긴다.
