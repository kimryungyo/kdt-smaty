# 워크플로우 API 계약

아래는 목표 계약이다. 현재 `/api/status`, `/api/control`, `/api/target`, `/api/profiles`와
WLED API는 구현돼 있지만 사용자·Vision·자동화·preset 계약은 아직 없다.

## 현재 사용자와 Vision

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/current-user` | 얼굴로 확정한 현재 사용자 read-only 조회 |
| `GET` | `/api/vision/status` | 안정화된 신원·재실·자세와 freshness 조회 |

현재 사용자를 변경하는 PUT/DELETE API는 제공하지 않는다.

`CurrentUserSnapshot`의 최소 필드는 `profileId`, `status`, `observedAt`, `expiresAt`이다.
status는 `RECOGNIZED`, `UNREGISTERED`, `MULTIPLE`, `UNKNOWN`, `VACANT`를 사용한다.

## 얼굴 등록

| Method | 경로 | 목적 |
| --- | --- | --- |
| `POST` | `/api/profiles/{id}/face-enrollments` | 등록 session 시작 |
| `GET` | `/api/face-enrollments/{id}` | 진행 상태 조회 |
| `DELETE` | `/api/face-enrollments/{id}` | 진행 중 등록 취소 |
| `DELETE` | `/api/profiles/{id}/face` | 저장된 얼굴 등록 제거 |

시작은 `202 Accepted`, 상태 조회는 `200`이다. 다른 등록 진행 중이면 `409`, profile 없음은
`404`, camera/Vision 미준비는 `503`이다.

## 제어 모드와 자동화

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/automation/status` | mode, 자동화 상태와 차단 이유 조회 |
| `PUT` | `/api/desk/mode` | 현재 사용자 session의 `AUTO`/`MANUAL` 변경 |
| `PUT` | `/api/profiles/{id}/automation` | profile 자세 유지 시간 변경 |

현재 사용자가 없거나 fresh하지 않으면 mode 변경을 `409`로 거부한다. `AUTO` 요청은 기존
이동을 STOP하고 자세 후보를 초기화한 뒤 성공한다.

## 사용자 preset 설정

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/profiles/{id}/presets` | 설정 화면용 사용자 preset 목록 |
| `POST` | `/api/profiles/{id}/presets` | 이름·높이 preset 생성 |
| `PATCH` | `/api/presets/{presetId}` | 이름 또는 높이 수정 |
| `DELETE` | `/api/presets/{presetId}` | preset 삭제 |

이 API는 profile 설정용이며 호출해도 현재 사용자와 제어 모드를 바꾸지 않는다.

## 현재 사용자 preset 실행

| Method | 경로 | 목적 |
| --- | --- | --- |
| `GET` | `/api/desk/presets` | 자세별·사용자 preset 합성 목록 |
| `POST` | `/api/desk/presets/{presetKey}/apply` | MANUAL 전환 후 preset 목표 접수 |

합성 항목은 `key`, `kind`, `name`, `heightCm`, `editable`을 가진다. 자세 key는
`posture:sitting`, `posture:standing`이고 사용자 항목은 preset ID를 사용한다.

적용 성공 `200`은 목표 접수 의미다. 현재 사용자 없음·불안정, 다른 사용자 소유 preset 또는
오래된 화면 요청은 `409`, 높이·릴레이 미준비는 `503`이다.

## 기존 책상 명령과의 연결

현재 `/api/control`과 `/api/target`은 Dashboard가 `DeskController`에 직접 위임한다. 목표
구조에서는 상위 사용자 명령이 먼저 서버 mode를 `MANUAL`로 전환하도록
`AutomationService` 경계를 거친다. wire MQTT 계약과 `DeskController` 안전 검증은 바꾸지
않는다.
