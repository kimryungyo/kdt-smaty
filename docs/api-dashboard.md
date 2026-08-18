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
  "relay": {"event": "online", "state": "STOP", "firmware": "smartdesk-fin-relay-1.0.5", "code": null, "detail": null, "receivedAt": "2026-08-08T00:00:00Z", "lastError": null},
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

profile CRUD와 상태 조회는 전역 application readiness를 일괄 검사하지 않는다. 현재
`sittingHeightCm`, `standingHeightCm`, `ledColor`는 내장 `기본` 작업 모드로 사용하며 custom
작업 모드·현재 session·자동화의 목표 API는 [워크플로우 API 계약](workflow/api-contracts.md)을
따른다.

## WLED 전체 조명

WLED는 선택 장치다. `GET /api/wled/status`는 비활성화 시에도 `200`과
`{"status":"DISABLED", ...}`를 반환하며, 연결 실패도 마지막 관측값을 포함한
`status: "ERROR"` snapshot으로 반환한다. snapshot의 `brightness`는 장치가 보고한
master 밝기 0~255다. 따라서 이 상태는 Desk polling에 포함하지 않는다.

`GET /api/wled/capabilities`는 장치의 effect/palette 목록을 반환한다. `POST /api/wled/control`
은 아래 네 요청만 받으며, 성공은 WLED가 전체 유효 segment에 요청 값을 적용했다고
응답으로 확인한 경우다.

```json
{"action":"OFF"}
{"action":"BRIGHTNESS","brightness":64}
{"action":"SOLID","color":"FF3000"}
{"action":"EFFECT","effectId":42,"paletteId":6,"speed":160,"intensity":128,"color":"FF3000"}
```

`BRIGHTNESS`는 전원과 segment의 색상·effect를 바꾸지 않고 master 밝기만 적용한다.
응답의 `brightness`가 요청값과 일치할 때만 성공한다.

지원하지 않는 effect/palette는 `409`, 장치 미연결은 `503`, 잘못된 장치 응답은 `502`이다.

## 데스크 틸팅 (하드웨어 준비 인터페이스)

틸팅 actuator는 아직 연결하지 않았지만, UI와 이후 firmware가 같은 계약을 사용할 수
있도록 endpoint를 먼저 고정한다. `GET /api/tilt/status`는 아래처럼 현재 상태를
`200`으로 반환한다.

```json
{
  "status": "UNAVAILABLE",
  "level": null,
  "targetLevel": null,
  "minLevel": 0,
  "maxLevel": 5,
  "detail": "틸팅 하드웨어가 아직 연결되지 않았습니다.",
  "lastError": null,
  "updatedAt": "2026-08-18T00:00:00Z"
}
```

`PUT /api/tilt/target`은 `{ "level": 0..5 }`를 받는다. actuator service를 연결하기
전에는 요청을 성공으로 가장하지 않고 `503`을 반환한다. 실제 구현 시에도 이 응답
모델과 단계 범위는 유지하며, 안전 interlock과 정지 정책은 그 hardware 작업에서
명시한다.
