# SMART DESK 워크플로우

이 디렉터리는 Dashboard 사용자 행동과 서버 내부 기능 흐름을 함께 설계하되, 서로 다른
상태 소유권을 한 문서에 섞지 않도록 영역별로 나눈다.

현재 구현과 목표를 다음 표기로 구분한다.

- **현재**: 코드와 API가 존재한다.
- **연결 예정**: 기반 코드나 화면은 있지만 도메인 연결이 없다.
- **신규 설계**: 모델, 저장소 또는 API부터 구현해야 한다.

## 문서 안내

| 문서 | 소유하는 내용 |
| --- | --- |
| [Dashboard 워크플로우](dashboard.md) | 화면 이동, profile 설정, 상태·AI 응답 표시 |
| [사용자 식별과 Vision](identity-and-vision.md) | 얼굴 등록·식별, 재실·자세, 서버 현재 사용자 |
| [책상 제어와 프리셋](desk-control.md) | `AUTO`/`MANUAL`, 자세 자동화, 높이 preset |
| [API 계약](api-contracts.md) | 제안 endpoint, 응답 모델과 오류 의미 |
| [구현 계획](implementation-plan.md) | 현재 차이, 구현 순서와 검증 시나리오 |

책상 relay 자체의 물리 안전과 pulse 정책은
[책상 제어와 안전](../architecture/desk-safety.md)을 기준으로 한다. Voice 내부 상태 머신은
[AI 음성 스피커](../architecture/ai-voice-assistant.md)를 기준으로 한다.

## 공통 원칙

1. Dashboard의 page 상태와 서버의 사용자·Vision·제어 상태를 합치지 않는다.
2. Dashboard에서 연 profile은 설정 대상일 뿐 현재 사용자가 아니다.
3. 현재 사용자는 서버가 안정화한 얼굴 식별 결과로만 결정한다.
4. 얼굴 식별, 재실·자세 판정과 자동화는 Dashboard가 닫혀도 계속 동작한다.
5. Dashboard는 명령 API를 호출하고 서버 snapshot을 표시한다. 장기 작업과 정책 판단은
   서버가 담당한다.
6. 책상 `AUTO`/`MANUAL` 모드는 서버가 소유한다. 새 사용자 session은 `AUTO`로 시작한다.
7. preset, 직접 목표, HOLD와 STOP 같은 수동 명령은 먼저 `MANUAL`로 전환한다.
8. 얼굴·자세·센서·릴레이 중 하나라도 불확실하거나 오래됐으면 자동 이동하지 않는다.
9. 실제 이동은 자동화와 Dashboard 모두 `DeskController`를 통해 요청한다.
10. 얼굴 원본과 crop은 기본 저장하지 않고 등록 임베딩과 최소 메타데이터만 저장한다.
11. WLED와 Voice는 필수 lifecycle 서비스로 시작한다.

## 전체 흐름

```text
FastAPI lifespan
  ├─ SQLite / MQTT / Desk
  ├─ CameraPublisher / RtspFrameSource
  ├─ 얼굴 식별 / 재실 / 자세 / CurrentUser
  ├─ AutomationService
  └─ WLED / Voice

Dashboard
  ├─ profile과 얼굴 등록 설정
  ├─ 서버 상태와 높이 preset 표시
  ├─ 수동 명령 전달
  └─ AI 화면 응답 표시

서버 background Vision
  → 얼굴로 현재 사용자 확정
  → 사용자 profile 조회
  → AUTO 자세 정책 또는 MANUAL 명령
  → DeskController
```

## 책임 경계

| 책임 | Dashboard | 서버 |
| --- | --- | --- |
| profile 목록·폼 | 표시·입력 | 저장·검증 |
| profile 설정 대상 | 브라우저 UI 상태 | 현재 사용자에 반영하지 않음 |
| 얼굴 등록 | 시작·취소와 진행 표시 | frame 수집·임베딩 추출·저장 |
| 얼굴 식별 | read-only 표시 | background 추론·안정화 |
| 현재 사용자 | read-only 표시 | 얼굴 식별로 단독 결정 |
| 자세·재실 | read-only 표시 | background 판정·freshness |
| 제어 모드 | 변경 명령·표시 | session 상태 소유 |
| 자동 높이 | 상태·차단 이유 표시 | Vision·profile·Desk 조합 |
| 책상 이동 | 사용자 명령 전달 | 안전 검증·실행 |
| AI 응답 | 화면용 결과 표시 | 대화·도구·camera 문맥 처리 |

Dashboard polling이 중단돼도 사용자 이탈로 간주하지 않는다. 현재 사용자 해제와 안전 STOP은
서버의 재실·freshness 정책이 결정한다.
