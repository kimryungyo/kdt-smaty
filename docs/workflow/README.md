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
| [책상 제어 방식과 작업 모드](desk-control.md) | `AUTO`/`MANUAL`, 활동별 높이·LED와 자세 자동화 |
| [API 계약](api-contracts.md) | 제안 endpoint, 응답 모델과 오류 의미 |
| [구현 계획](implementation-plan.md) | 현재 차이, 구현 순서와 검증 시나리오 |

책상 relay 자체의 물리 안전과 pulse 정책은
[책상 제어와 안전](../architecture/desk-safety.md)을 기준으로 한다. Voice의 현재 legacy
기준선은 [AI 음성 스피커](../architecture/ai-voice-assistant.md), 전환 목표와 구현 우선순위는
[Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)을 기준으로 한다.

## 공통 원칙

1. Dashboard의 page 상태와 서버의 사용자·Vision·제어 상태를 합치지 않는다.
2. Dashboard에서 연 profile은 설정 대상일 뿐 현재 사용자가 아니다.
3. 현재 session은 서버가 안정화한 재실과 얼굴 식별로만 결정한다. 등록 사용자가 확정되지
   않아도 단일 재실이면 익명 session을 만들며 Dashboard가 사용자를 선택하지 않는다.
4. 얼굴 식별, 재실·자세 판정과 자동화는 Dashboard가 닫혀도 계속 동작한다.
5. Dashboard는 명령 API를 호출하고 서버 snapshot을 표시한다. 장기 작업과 정책 판단은
   서버가 담당한다.
6. `controlMode`는 `AUTO`/`MANUAL` 제어 방식이고, `activityMode`는 활동별 높이·LED 묶음이다.
   서버가 둘을 독립적으로 소유하며 새 등록·익명 session은 `AUTO`로 시작한다.
7. 직접 목표, HOLD와 사용자 STOP은 먼저 `MANUAL`로 전환하되 active 작업 모드는 유지한다.
   작업 모드 전환은 control mode를 바꾸지 않는다. session이 없어도 HOLD, 직접 목표와 STOP은 허용한다.
8. 최초 이동 이후 자세 전환과 사용자의 AUTO 재활성화는 등록·익명 모두 fresh 자세 5초를
   확인하며 profile별 시간 설정은 두지 않는다.
9. Vision 불확실성은 AUTO만, 센서·MQTT·릴레이 안전 오류는 자동·수동 이동을 모두 차단한다.
10. 실제 이동은 자동화와 Dashboard 모두 `DeskController`를 통해 요청한다.
11. 얼굴 원본과 crop은 기본 저장하지 않고 등록 임베딩과 최소 메타데이터만 저장한다.
12. Arduino 높이와 Wi-Fi/MQTT ESP32 상태는 모든 이동에 필수다. WLED와 Voice는 선택 기능이며
    장애가 핵심 Desk·profile·Dashboard readiness를 내리지 않는다.
13. Agent function tool은 기존 public domain service만 호출하고 물리 부작용 직전에
    `sessionId`와 안전 정책을 재검증한다.
14. 사용자 session 교대·종료는 이전 Agent run·TTS·follow-up과 SDK 대화 session을 취소·폐기한다.

## 전체 흐름

```text
FastAPI lifespan
  ├─ SQLite / MQTT / Desk
  ├─ CameraPublisher / RtspFrameSource
  ├─ 얼굴 식별 / 재실 / 자세 / CurrentUser
  ├─ AutomationService
  └─ WLED / Agents Voice runtime / Voice

Dashboard
  ├─ profile과 얼굴 등록 설정
  ├─ 서버 상태와 제어 방식·작업 모드 표시
  ├─ 수동 명령 전달
  └─ AI 화면 응답 표시

서버 background Vision
  → 상단 몸체/얼굴 + 하단 하체로 단일 재실 확정
  → 등록 또는 익명 사용자 session 생성
  → 등록 profile의 기본 작업 모드 또는 익명 기본 75/110cm 선택
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
| 현재 사용자 | read-only 표시 | 재실·얼굴로 등록/익명 session 결정 |
| 자세·재실 | read-only 표시 | background 판정·freshness |
| 제어 방식 | 변경 명령·표시 | session의 `controlMode` 소유 |
| 작업 모드 | 선택·표시 | 등록 session의 active 설정 snapshot 소유 |
| 자동 높이 | 상태·차단 이유 표시 | Vision·profile·Desk 조합 |
| 책상 이동 | 사용자 명령 전달 | 안전 검증·실행 |
| AI 응답 | 화면용 결과 표시 | 대화·도구·camera 문맥 처리 |

Dashboard polling이 중단돼도 사용자 이탈로 간주하지 않는다. 현재 사용자 해제와 안전 STOP은
서버의 재실·freshness 정책이 결정한다.

## 확정된 session 요약

- 상단 책상 ROI의 몸체 또는 얼굴 한 명과 하단 하체 한 명, 자세가 3초 안정화되면 session을
  시작한다.
- 등록 얼굴이면 profile session, 아니면 익명 session이며 익명 높이는 앉음 75cm·섬 110cm다.
- 최초 AUTO 목표는 session 시작 뒤 2초 동안 조건이 유지돼야 한다.
- 이후 자세 전환과 같은 session의 명시적 AUTO 재활성화는 자세를 5초 확인한다.
- 얼굴이 안 보여도 fresh 단일 재실이 이어지면 session과 AUTO를 유지한다.
- 다중·count 불일치는 session을 유지하고 AUTO만 STOP하며 수동 제어는 허용한다.
- 안정 VACANT로 session이 끝난 뒤 fresh VACANT 30초가 이어지면 75cm park를 시도한다.
- 사용자 종속 control/activity mode·Voice Desk 명령은 `expectedSessionId`를 검증하고 STOP은 항상
  우선한다.
- 책상 session, Agents SDK 대화 session과 짧은 VoicePipeline 실행은 서로 다른 수명이다.
- session 없음·다중 상태의 일반 질문은 임시 비개인화 session만 사용하고 기존 사용자
  대화·Mem0에 접근하지 않는다.
