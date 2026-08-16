# 02. 필수 서비스 수명주기

## 사용자 결과

서버를 정상 구성해 실행하면 WLED와 Voice가 항상 함께 시작된다. 기능을 켜기 위해 별도
`enabled` 설정을 찾을 필요가 없으며, 구성 오류나 장치 단절은 서비스가 조용히 사라지는
대신 명확한 시작 실패 또는 기능별 오류 상태로 나타난다.

이 작업은 [상태·워크플로우 계약 확정](01-workflow-contracts.md)과 독립적으로 착수할 수 있다.
Voice의 최종 조립 대상은 legacy `AssistantService`가 아니라
[Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)의
`AgentsVoiceRuntime`이다. 공통 lifecycle 정리는 먼저 할 수 있지만 폐기 예정 gateway와 수동
tool registry를 새 필수 구조로 굳히지 않는다.

## 현재 상태

- `bootstrap.py`는 `settings.wled.enabled`와 `settings.voice.enabled`일 때만 객체를 만든다.
- Voice dependency 생성 예외는 로그만 남기고 서버 조립을 계속할 수 있다.
- WLED가 없으면 legacy Voice tool registry에서도 WLED tool을 생략한다.
- Voice는 오디오 장치 hot-plug 복구 경로가 있지만 시작 구성과 runtime 장애의 의미가
  문서에서 충분히 분리되지 않았다.
- Voice debug HTTP 서버는 운영 Voice와 달리 선택 기능으로 유지할 필요가 있다.

## 정책 기준

“필수”는 WLED와 Voice를 항상 생성하고 lifecycle에 등록한다는 뜻이다. 다만 물리 장치가
일시적으로 응답하지 않는 모든 경우에 FastAPI 전체를 종료한다는 뜻은 아니다.

다음 실패 분류를 구현 전에 확정한다.

| 실패 | 기본 처리 방향 |
| --- | --- |
| OpenAI key, WLED URL, 모델 파일 같은 필수 구성 누락 | 설정 검증 또는 시작 실패 |
| Agents SDK·Python dependency·모델 load 실패 | 시작 실패, 이미 시작한 resource 정리 |
| 오디오·WLED 장치 일시 단절 | 기능별 `ERROR`/`DEGRADED`, 재시도 가능 여부 표시 |
| 실행 중 background task 비정상 종료 | critical task 정책과 health에 반영 |
| Voice debug 비활성 | 정상이며 운영 Voice 시작과 무관 |

장치 단절을 degraded로 허용하더라도 객체 생성이나 `start()` 자체를 건너뛰어서는 안 된다.

## 구현 단계

### 설정과 조립

- [ ] WLED·Voice `enabled` 필드를 제거하거나 더 이상 분기 조건으로 사용하지 않는다.
- [ ] 기존 환경변수의 제거·호환 정책을 정하고 `.env.example`을 갱신한다.
- [ ] 필수 OpenAI key, model/effect 파일과 URL을 가능한 범위에서 시작 전에 검증한다.
- [ ] 최종 구조의 `AppContainer.wled`, `agents_voice_runtime`, `voice`를 정상 조립에서는
  non-optional로 전환한다. legacy `assistant`를 새 필수 경계로 만들지 않는다.
- [ ] WLED public service를 감싼 Agents SDK function tool을 Agent factory에 항상 등록한다.
- [ ] dependency 생성 실패를 삼키지 않고 원인과 resource 이름을 보존해 전파한다.
- [ ] Agents SDK client·VoicePipeline·Agent factory는 application당 한 번 조립하고 SDK 객체를
  `VoiceService`와 사용자 session service에 흩어 놓지 않는다.

### lifecycle과 상태

- [ ] SQLite·MQTT·Desk·카메라·WLED·Mem0·Agents Voice runtime·Voice의 시작·종료 순서를
  재검토한다.
- [ ] 일부 resource 시작 실패 시 시작 완료 resource가 역순으로 한 번씩 종료되게 한다.
- [ ] WLED와 Voice의 시작 실패·runtime 단절을 health와 Dashboard 상태에 구분해 표현한다.
- [ ] runtime 재연결을 지원하는 장치는 서버 재시작 없이 회복되는지 확인한다.
- [ ] Voice debug만 별도 `enabled`를 유지하고 Voice가 없는 상태를 전제로 하지 않게 한다.

### 문서와 운영

- [ ] 개발·운영 최소 환경변수와 필수 파일을 README에 정리한다.
- [ ] WLED 미응답, 오디오 장치 없음과 OpenAI 오류의 확인·복구 절차를 기록한다.
- [ ] 테스트에서 실제 장치 없이 사용할 fake resource 조립 방법을 유지한다.

## 제외 범위

- WLED 효과 정책과 profile별 자동 조명 적용
- VoicePipeline, Agent workflow와 대화 상태의 내부 구현(task 08 범위)
- 얼굴 기반 Voice 기억과 Dashboard AI 응답
- Voice debug 서버를 운영 Dashboard로 통합하는 작업

## 검증

- 설정값으로 WLED 또는 Voice lifecycle 등록을 생략할 수 없다.
- 정상 lifespan에서 두 resource의 `start()`와 `stop()`이 각각 한 번 호출된다.
- WLED 또는 Voice 시작 실패 시 뒤 resource는 시작되지 않고 앞 resource는 정리된다.
- Agents SDK 또는 native voice dependency import·조립 실패도 선택적 기능 누락이 아니라
  구체적인 시작 실패로 나타난다.
- 필수 구성 누락은 애플리케이션 요청 처리 전에 구체적인 원인으로 실패한다.
- 일시 장치 단절은 확정한 정책에 따라 기능별 상태와 재연결로 나타난다.
- 기존 Voice 단위 테스트, WLED API 계약과 전체 application lifecycle 테스트가 통과한다.

## 완료 조건

- 정상 서버 실행마다 WLED와 Voice 시작이 반드시 시도된다.
- 서비스가 생성되지 않은 상태를 정상 운영 상태로 표현하지 않는다.
- 구성 오류, 시작 실패와 runtime 단절의 처리 차이가 코드·health·문서에 일치한다.
- 실패 후 background task, 오디오 stream, HTTP client와 장치 handle이 남지 않는다.
