# OpenAI Agents SDK 음성 파이프라인 전환 결정

**결정 기준일:** 2026-08-16
**문서 상태:** 확정된 전환 목표 — `main`의 현재 구현은 아직 legacy 경로
**구현 브랜치:** `feat/agents-sdk-voice-pipeline`

이 문서는 현재 구현된 수동 STT → Responses → TTS 파이프라인을 OpenAI Agents SDK의
`VoicePipeline` 중심 구조로 교체할 때 따라야 할 확정안을 기록한다. 현재 코드의 기준선과
세부 동작은 [기존 AI 스피커 설계](ai-voice-assistant.md)에 남기되, 두 문서가 충돌하면
Agents SDK 교체 범위에서는 이 문서가 우선한다.

문서 우선순위는 범위별로 적용한다. 이 문서는 Voice runtime, model·오디오, tool 실행 구조,
SDK 대화 session과 Mem0 adapter의 기준이다. [task 01 상태·워크플로우 계약](../tasks/01-workflow-contracts.md)은
사용자·재실 session, `expectedSessionId`, 물리 제어와 STOP 안전 계약의 기준이며 이 문서도
그 경계를 우회하지 않는다.

## 1. 확정 결정

| 항목 | 확정안 |
| --- | --- |
| 음성 구조 | local Wake Word → Agents SDK `VoicePipeline`의 streaming STT → Agent workflow → streaming TTS |
| Agent model | `gpt-5.6-terra`, `reasoning.effort="low"` |
| STT model | `gpt-4o-transcribe` |
| TTS model | `tts-1` |
| microphone | 24kHz mono PCM16으로 한 번만 capture |
| Wake Word | 기존 local detector 유지, 24kHz 입력을 SoXR로 16kHz 변환 |
| 발화 종료 | `server_vad`를 명시적으로 사용 |
| 초기 VAD 값 | `threshold=0.5`, `prefix_padding_ms=300`, `silence_duration_ms=600` |
| 대화 방식 | 초기에는 half-duplex 유지, barge-in과 AEC 제외 |
| 후속 발화 | Agent가 필요할 때만 `request_followup` 제어 tool로 요청 |
| Session memory | 책상 사용자 `sessionId` 단위의 Agents SDK memory session |
| Long-term memory | 등록 사용자 `profileId` 단위의 Mem0 OSS |
| Mem0 배포 | Main process와 같은 `fin-main` container에 library로 포함, 데이터만 volume으로 영속화 |
| 기존 gateway | `OpenAiGateway`와 수동 Responses/tool loop 제거 |
| 전환 방식 | 별도 feature branch를 rollback 경계로 사용하고 legacy/SDK 이중 실행 flag는 두지 않음 |

OpenAI 공식 문서 기준으로 `gpt-5.6-terra`는 성능과 비용 균형형 모델이고 low reasoning은
latency가 중요한 작업의 선택지다. `gpt-4o-transcribe`는 기존 Whisper 계열보다 정확도와
언어 인식이 개선된 STT 모델이며, `tts-1`은 realtime TTS 사용에 맞춰 속도를 우선한다.

## 2. 목표 구조

```text
Microphone 24kHz PCM16
  │
  ├─ SoXR 24kHz → 16kHz ─→ local Wake Word detector
  │
  └─ Wake Word 이후 원본 24kHz chunk
           ↓
      AgentsVoiceRuntime
           └─ VoicePipeline
              ├─ gpt-4o-transcribe + server_vad
              ├─ SmartDeskVoiceWorkflow
              │   ├─ Agent(gpt-5.6-terra / low)
              │   ├─ Agents SDK session
              │   ├─ hosted/function tools
              │   └─ Mem0 MemoryService
              └─ tts-1 streaming PCM
                       ↓
              PlaybackCoordinator
                       ↓
                    Speaker
```

오디오 연결과 대화 memory의 수명은 분리한다. `VoicePipeline` 실행은 Wake Word와 조건부
follow-up 묶음마다 짧게 만들고 종료하지만, Agents SDK session은 현재 책상 사용자
`sessionId`가 유지되는 동안 여러 음성 실행에서 재사용한다.

즉 책상 사용자 session 하나에는 여러 Agent turn과 여러 짧은 `VoicePipeline` 실행이 들어간다.
Agents SDK session은 이 turn들 사이의 단기 대화 문맥이고, `profileId`로 영속하는 Mem0 장기
기억과는 별도다.

## 3. 책임 경계

### `VoiceService`

- microphone, Wake Word, 효과음, half-duplex 상태와 speaker 재생을 조정한다.
- Wake Word 전에는 local detector만 사용한다.
- Wake Word 후 원본 24kHz PCM chunk를 `AgentsVoiceRuntime`에 전달한다.
- TTS 중 microphone 입력을 mute 또는 discard하고 정상 drain 뒤에만 follow-up을 연다.
- OpenAI SDK 타입, Agent tool registry와 memory backend를 직접 소유하지 않는다.

### `AgentsVoiceRuntime`

- Agents SDK 객체와 `AsyncOpenAI` client의 생명주기를 소유한다.
- `StreamedAudioInput`과 SDK audio stream을 프로젝트 오디오 경계에 연결한다.
- SDK lifecycle·transcript·audio·usage·오류 event를 프로젝트 snapshot과 오류로 변환한다.
- STT, TTS, VAD와 Agent model 설정을 한곳에서 조립한다.
- 향후 모델 routing이 필요할 때 Voice 하드웨어 계층을 바꾸지 않고 확장할 경계다.

`AgentsVoiceRuntime`은 기존 `OpenAiGateway`처럼 `transcribe()`,
`create_response_step()`, `synthesize()`를 각각 노출하지 않는다. 세 단계는 하나의 SDK
pipeline 실행으로 묶는다.

### `SmartDeskVoiceWorkflow`

기본 `SingleAgentVoiceWorkflow` 대신 프로젝트용 workflow를 둔다.

- 책상 사용자 snapshot과 Agents SDK session 선택
- `gpt-5.6-terra / low` Agent 실행
- SDK hosted tool과 local function tool 연결
- tool 실행 전 진행 안내 음성 처리
- 조건부 follow-up 신호 처리
- session 변경·취소와 memory read/write 정책 적용
- tool 이름, 지연, token usage를 debug snapshot으로 전달

### `PlaybackCoordinator`

SDK가 생성한 audio chunk와 local 효과음을 순차 재생한다. TTS model이나 Agent tool을
직접 알지 않으며, local speaker 단독 출력과 drain·cancel 책임을 유지한다.

## 4. 오디오, STT와 VAD

### 단일 24kHz capture

microphone을 Wake Word용 16kHz와 OpenAI용 24kHz로 두 번 열지 않는다. `LocalAudioInput`은
24kHz mono PCM16만 capture하고, 같은 chunk에서 Wake Word 입력만 16kHz로 변환한다.
Wake Word 이후에는 24kHz 원본을 SDK에 공급한다.

```text
24kHz microphone frame
  ├─ 원본 보존 ───────────→ VoicePipeline
  └─ stateful SoXR 16kHz ─→ Wake Word
```

Wake Word model은 16kHz 전용으로 유지한다. Raspberry Pi 5의 실제 resampling CPU와 장시간
audio underrun은 hardware test에서 측정하되, 현재 구조에서는 별도 microphone stream보다
stateful resampler가 단순하고 부하가 작다.

### STT와 발화 종료

STT model은 `gpt-4o-transcribe`로 명시한다. 실제 명령은 사용자가 말하는 동안 streaming
전송하지만 Agent 실행과 물리 tool 호출은 final transcript 이후에만 시작한다. partial
transcript를 근거로 책상 이동 같은 부작용을 실행하지 않는다.

SDK 기본값에 의존하지 않고 아래 `server_vad` 값을 초기 기준으로 고정한다.

```text
threshold             0.5
prefix_padding_ms     300
silence_duration_ms   600
```

600ms는 기존 local RMS recorder의 발화 종료 기준과 비교하기 위한 시작값이다. 문장 중간이
자주 끊기면 `silence_duration_ms`를 늘리고, 긴 설명과 머뭇거림에서 문제가 반복될 때만
`semantic_vad`와 A/B 비교한다. 선택한 STT model과 turn detection 조합은 OpenAI live
integration test에서 함께 검증한다.

## 5. Agent, tool과 중간 음성 안내

Agent는 `gpt-5.6-terra`와 low reasoning을 기본 profile로 사용한다. model ID와 reasoning은
설정 객체로 분리해 향후 단순 명령과 복잡한 Vision 작업을 routing할 수 있게 하되, 초기에는
하나의 profile만 구현한다.

검색이나 긴 tool 작업에서는 최종 결과까지 침묵하지 않는다.

```text
사용자: "내일 아침 날씨를 알려줘"
Agent:   "네, 내일 날씨를 찾아보겠습니다."
         ↓ 바로 TTS
Agent:   WebSearchTool 실행
Agent:   "내일 아침은 ...입니다."
         ↓ 최종 TTS
```

TTS 자체를 function tool로 만들지 않는다. workflow가 Agent의 text delta를
`VoicePipeline`에 전달하면 SDK TTS가 문장 단위로 audio를 생성한다. 모델이 긴 tool 호출
전에 안내를 누락한 경우에는 workflow가 일반적인 짧은 진행 안내를 보완할 수 있다. 안내는
성공을 미리 단정하지 않고 `확인해보겠습니다`처럼 실제 상태와 모순되지 않게 한다.

진행 안내 TTS, tool 상태와 최종 TTS는 모두 같은 프로젝트 `turnId`에 속한다. SDK의 한
`Runner` 실행이 여러 model 호출과 tool 호출을 포함하더라도 하나의 논리적 Assistant turn으로
취급하고, Dashboard event와 audio에도 동일한 `turnId`와 증가하는 sequence를 붙인다.

기존 `AssistantToolRegistry`, tool spec/call DTO와 수동 dispatch loop는 제거하고 Agents SDK
hosted tool 또는 `function_tool`로 옮긴다. 도메인 서비스와 오류 변환은 재사용한다.

- Web 검색: SDK hosted web search
- WLED: 기존 WLED public service를 감싼 function tool
- Desk: 향후 `AutomationService` public API만 호출하는 function tool
- Camera/crop/OCR: 해당 task가 구현될 때 function/hosted tool로 추가
- 장기 기억: Agent에 Mem0 SDK를 직접 노출하지 않고 `MemoryService` 정책을 통함

물리 부작용 tool은 model이 tool call을 생성하거나 argument validation을 끝낸 시점이 아니라
`AutomationService` public API를 실제 호출하기 직전에, turn 시작 시 캡처한 `sessionId`가
여전히 현재 session인지 검증한다. 검증 성공 자체를 이후 실행 권한으로 오래 보관하지 않는다.
STOP 성격의 안전 명령은 사용자 session과 무관하게 기존 안전 경계를 따른다.

## 6. 조건부 follow-up과 half-duplex

모든 답변 뒤 microphone을 여는 방식은 사용하지 않는다. Agent가 사용자의 추가 입력이
실제로 필요할 때만 비음성 제어 tool `request_followup`을 호출한다.

```text
일반 답변 완료
  └─ Wake Word 대기로 복귀

Agent가 request_followup 호출
  └─ TTS drain
     └─ 현재 사용자 session 재검증
        └─ 제한된 follow-up 창 시작
```

초기 follow-up window는 현재 구현의 4초를 유지하되 설정값으로 둔다. session 종료·전환,
TTS cancel 또는 Voice 오류가 발생하면 follow-up을 열지 않는다. 초기 구현은 half-duplex이며
TTS 중 사용자의 끼어들기, AEC와 barge-in은 범위에서 제외한다.

## 7. Session memory와 장기 기억

두 memory는 목적, key와 수명이 다르다.

| 구분 | Agents SDK session memory | Mem0 long-term memory |
| --- | --- | --- |
| 목적 | 현재 책상 사용 중 대화 흐름 | 여러 방문에 걸친 사용자 선호와 지속 사실 |
| key | `sessionId` | `profile:<profile_id>` |
| 저장 예 | 최근 질문·답변·tool 결과 | 선호 높이, 설명 방식, 명시적 사용자 정보 |
| 수명 | 현재 책상 사용자 session | 사용자가 삭제하거나 profile을 삭제할 때까지 |
| 서버 재시작 | 폐기 | 영속 저장소에서 복구 |
| 익명 사용자 | session 범위에서 사용 | 사용하지 않음 |

### 사용자 session 연결

- `REGISTERED`: `sessionId`용 SDK memory session과 `profileId`용 Mem0를 함께 사용한다.
- `ANONYMOUS`: `sessionId`용 SDK memory session만 사용한다.
- session 없음: Wake Word와 그 조건부 follow-up 묶음에만 유효한 임시 비개인화 session을
  사용하고 종료 후 폐기한다.
- `MULTIPLE` 또는 count 불일치: 기존 사용자 session memory는 보존하지만 읽거나 쓰지 않는다.
  일반 질문은 별도 임시 비개인화 session으로 처리한다.
- 익명→등록, A→B, A→익명, `VACANT` 종료: 항상 새 `sessionId`이므로 이전 SDK session을
  폐기한다. 같은 profile이 나중에 돌아와도 새 session이다.
- 서버 재시작: task01 계약대로 현재 사용자 session과 SDK session을 복원하지 않는다.

한 사용자가 5시간 책상을 쓰면 논리적으로 같은 SDK session을 사용하지만, 5시간 동안 모든
원문과 tool 결과를 매번 무제한 전송하지 않는다. 최근 대화 제한 또는 compaction을 adapter
내부 정책으로 추가한다. 구체적인 item/token 기준은 실제 사용량을 측정해 설정으로 확정한다.

### turn 경합 처리

1. turn 시작 시 `sessionId`, `profileId`와 현재 상태를 원자적으로 capture한다.
2. session 전환 경계에서는 이전 session을 먼저 무효화하고 비동기 cleanup을 시작한다.
3. 부작용 function tool은 실제 domain service 호출 직전에 같은 session이 유효한지 검증한다.
4. TTS 재생, follow-up 진입과 Mem0 저장 직전에 각각 정책과 session을 다시 검증한다.
5. session 종료·전환 event를 받으면 진행 중 Agent run, 아직 domain service를 호출하지 않은
   부작용 tool, 재생 중·대기 중 TTS와 follow-up을 취소한다.
6. 이전 `sessionId`의 SDK memory session을 폐기하고 이후 조회·저장을 거절한다.
7. 취소와 경합해 늦게 끝난 transcript, tool 결과, audio와 Dashboard event는 새 사용자
   응답이나 memory를 갱신하지 않는다.

`CurrentUserSessionService` 구현은 원자적 snapshot 조회, `sessionId` 검증과 변경 event 구독을
제공해야 한다. Agents SDK 객체는 사용자 session service에 넣지 않고 Voice memory adapter가
`sessionId`를 key로 생성·폐기한다.

내부 변경 event는 최소한 이전·현재 `sessionId`, 전환 이유, 단조 증가 sequence와 변경 시각을
전달한다. Voice runtime은 event sequence를 기준으로 중복·역순 전달을 안전하게 무시하되,
이미 무효화된 session의 실행을 재개하지 않는다. 구체적인 event bus 구현은 task 05에서
선택한다.

## 8. Mem0 저장과 배포

### 저장 정책

Mem0는 `explicit_only`로 시작한다.

- `기억해 줘`, `앞으로 이렇게 해 줘`처럼 사용자가 명시적으로 요청한 내용
- 사용자가 확인한 장기 선호와 지속적인 개인화 정보

다음은 저장하지 않는다.

- raw PCM과 전체 STT transcript
- 일회성 질문, 검색 결과와 tool 실행 log
- camera 원본·crop과 얼굴 image
- model이 추측했지만 사용자가 확인하지 않은 사실
- API key, 인증 정보와 민감 정보

허용된 turn만 Mem0 추론·저장 단계에 전달한다. 기억 검색·저장 실패는 Voice 전체를
실패시키지 않고 memory 없는 응답으로 degraded 처리한다. profile 삭제는 해당
`profile:<profile_id>` 기억 전체 삭제가 성공한 뒤에만 완료한다.

### 초기 운영

별도 Mem0 REST server를 만들지 않는다. `mem0ai` OSS `Memory` 또는 `AsyncMemory`를
`fin-main` process에 library로 포함한다.

```text
fin-main process/container
├─ Smart Desk application
├─ OpenAI Agents SDK
└─ mem0ai OSS
    └─ /app/data/mem0
       ├─ qdrant/
       └─ history.db
```

현재 host process 운영에서는 저장 경로를 `data/mem0`로 명시한다. Main을 Docker로 옮기면
같은 `fin-main` image/container에 `mem0ai`를 설치하고 `/app/data/mem0`만 named volume 또는
bind mount로 영속화한다. image layer, `/tmp`나 container writable layer에 기억 데이터를
두지 않는다.

Mem0를 직접 운영해도 현재 기본안의 기억 추출과 embedding 요청은 OpenAI provider를
사용한다. 모든 추론까지 offline으로 운영하는 결정은 포함하지 않는다.

다음 조건 중 하나가 실제로 생길 때만 Mem0를 별도 REST/Postgres deployment로 분리한다.

- Main을 여러 process 또는 container replica로 동시에 실행
- 여러 애플리케이션이나 장치가 같은 기억을 직접 사용
- Mem0 자체 관리 Dashboard와 API key/audit 기능 필요
- 독립 배포·확장과 중앙 vector store 필요

## 9. 교체 범위

| 현재 구성 | 처리 | 목표 구성 |
| --- | --- | --- |
| `OpenAiGateway` | 제거 | `AgentsVoiceRuntime` |
| `AssistantService` 수동 history/tool loop | 교체 | `SmartDeskVoiceWorkflow` + Agents SDK session |
| `AssistantToolRegistry`, spec/call | 제거 | SDK hosted/function tools |
| `WledAssistantTools` 도메인 처리 | 유지·adapter 변경 | WLED function tools |
| `RmsRecorder` | 제거 | OpenAI `server_vad` |
| `build_wav`, `AudioUtterance` | 제거 | 24kHz chunk 직접 streaming |
| `LocalAudioInput` | 유지 | SDK input용 NumPy PCM chunk 제공 |
| Wake Word detector | 유지 | 24→16kHz SoXR 경로 유지 |
| `PlaybackCoordinator` | 유지·수정 | SDK audio event 재생 |
| `VoiceService` | 유지·단순화 | hardware, state와 half-duplex 조정 |
| `OpenAiTurnError` | 교체 | provider 중립 `VoiceTurnError` |
| `AssistantReply.next_action` | 교체 | `request_followup` control tool |

초기 dependency 기준은 다음 조합으로 lock과 전체 test를 다시 생성한다.

```text
openai-agents[voice] >=0.21,<0.22
openai              >=3,<4
numpy               >=2.2,<3
sounddevice, livekit-wakeword, soxr 유지
```

현재 `openai>=2.53,<3`, `numpy>=2.1` 제약을 그대로 둔 채 Agents SDK를 추가하지 않는다.
dependency 해석 결과와 native audio import는 x86 개발 환경과 Raspberry Pi 운영 환경에서
각각 검증한다.

## 10. 구현 순서

1. Agents SDK와 OpenAI SDK dependency를 정리하고 lock/install 검증
2. model·STT·TTS·VAD와 memory 설정 분리
3. Agent factory, hosted tool과 WLED function tool 구현
4. `SmartDeskVoiceWorkflow`와 `AgentsVoiceRuntime` 구현
5. 책상 사용자 session adapter와 SDK memory session 연결
6. `VoiceService`를 `VoicePipeline` 중심으로 교체
7. Mem0 `MemoryService` adapter와 embedded local storage 연결
8. 기존 gateway, 수동 tool loop, RMS recorder와 WAV STT 경로 삭제
9. fake SDK 경계의 unit test와 opt-in OpenAI/audio hardware integration test
10. latency, 한국어 STT, VAD, tool 안내와 장시간 memory 실측

## 11. 검증 기준

- Wake Word는 16kHz 입력을 받고 STT는 24kHz 원본 chunk를 받는다.
- partial transcript로 Desk 또는 WLED 부작용을 실행하지 않는다.
- `server_vad` 값이 SDK 기본값과 무관하게 명시적으로 적용된다.
- 긴 tool 호출 전에 짧은 진행 안내가 먼저 재생되고 최종 결과와 모순되지 않는다.
- 일반 답변은 Wake Word로 복귀하고 `request_followup`이 있을 때만 follow-up을 연다.
- A와 B의 SDK session 및 Mem0 namespace가 섞이지 않는다.
- 익명·session 없음·다중 상태에서 profile memory를 읽거나 저장하지 않는다.
- session 전환 뒤 늦은 tool, TTS, follow-up과 Mem0 write가 폐기된다.
- Mem0 장애가 STT, 기본 Agent 응답, TTS와 안전 STOP을 막지 않는다.
- container recreate 뒤 Mem0 volume은 유지되고 profile 삭제 시 해당 기억이 제거된다.
- dependency import, 전체 자동 test와 Raspberry Pi microphone/speaker 실측이 통과한다.

## 12. 구현 중 조정 가능한 값

아래 항목은 큰 제품 결정이 아니라 실측과 운영으로 조정할 설정이다.

- `tts-1` voice
- VAD threshold, prefix padding과 silence duration
- session history item/token 제한과 compaction 시점
- follow-up window
- Mem0 search limit, timeout과 backup 주기
- 향후 명령 복잡도 기반 model routing threshold

## 참고 자료

- [OpenAI GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [OpenAI GPT-4o Transcribe](https://developers.openai.com/api/docs/models/gpt-4o-transcribe)
- [OpenAI TTS-1](https://developers.openai.com/api/docs/models/tts-1)
- [OpenAI Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad)
- [OpenAI Voice Agents](https://developers.openai.com/api/docs/guides/voice-agents)
- [OpenAI Agents SDK 실행](https://developers.openai.com/api/docs/guides/agents/running-agents)
- [Mem0 OSS Python quickstart](https://docs.mem0.ai/open-source/python-quickstart)
- [Mem0 self-hosted setup](https://docs.mem0.ai/open-source/setup)
