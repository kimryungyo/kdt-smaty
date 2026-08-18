# 08. Agents SDK 음성과 AI 사용자 문맥

Agents SDK 교체의 model, VoicePipeline, session adapter와 Mem0 배포 기준은
[Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)을 따른다.

## 사용자 결과

Voice는 등록 session에서만 해당 사용자의 기억과 profile 설정을 사용한다. 익명 session은
일반 대화와 session 범위의 짧은 history만 사용하고 장기 기억을 읽거나 저장하지 않는다.
상단 다중에서는 이전 사람의 개인화를 일시 차단한다. 음성 답변과 함께 필요한 상세
정보는 같은 Assistant turn으로 Dashboard에 표시된다.

## 현재 상태

- wake word, 녹음, Assistant 호출, playback과 WLED function tool 기반이 구현돼 있다.
- 운영 Voice는 Agents SDK `VoicePipeline`과 `AgentsVoiceRuntime.run_audio` 하나로 실행한다.
- Voice runtime은 turn 시작 시 서버 current user session을 캡처해 context와 Assistant turn에 쓴다.
- profile별 장기 기억 service와 explicit-only 저장 정책이 구현돼 있다.
- `GET /api/assistant/latest`은 현재 session의 최신 Assistant turn 하나를 반환한다.
- Assistant final은 streaming으로 모은 답변만 요약(최대 200자)·상세로 기록하며 raw transcript는 turn에 기록하지 않는다.
- WLED와 Desk 사용자 명령 tool은 각각 public service와 `AutomationService` 경계에 연결돼 있다.

## 사용자 귀속 원칙

- Dashboard에서 편집한 profile은 Voice 사용자 문맥이 아니다.
- fresh하고 사용 가능한 current user session에서만 `profile:<profile_id>` 기억을 사용한다.
- 익명 session은 profile memory 없이 `sessionId` 범위의 짧은 history만 유지한다.
- Agents SDK session 객체는 사용자 session service가 소유하지 않는다. Voice memory adapter가
  책상 `sessionId`를 key로 생성·재사용·폐기한다.
- session 없음과 상단 다중 중 일반 질문은 해당 Wake Word/follow-up 묶음에만 유효한
  임시 비개인화 session으로 처리한다.
- Assistant turn 시작 시 `sessionId`와 profile ID를 캡처한다.
- session 변경 event를 받으면 이전 Agent run, 실행되지 않은 부작용 tool, TTS와 follow-up을
  취소하고 해당 SDK 대화 session을 폐기한다.
- turn 완료 시 session이 바뀌거나 정책상 불확실해졌다면 이전 profile 장기 기억에 저장하지
  않는다.
- 화면 응답 delivery ID와 기억 user ID를 혼동하지 않는다.
- 익명 또는 session 없음에서도 일반 비개인화 질문을 허용하되 사용자별 기억은 사용하지 않는다.

## 삭제와 화면 노출 정책

- 장기 기억은 기존 설계대로 등록 사용자, `explicit_only`, raw transcript 비저장을 유지한다.
- profile 삭제는 `profile:<profile_id>` 장기 기억 전체 삭제를 먼저 완료한 뒤 얼굴·작업 모드와
  profile row를 삭제한다. 기억 삭제 실패 시 profile DB는 유지하고 `503`으로 재시도를
  안내한다.
- current `sessionId`가 다른 값으로 바뀌거나 `null`이 되면 Dashboard에서 직전 session의 AI
  상세 응답을 즉시 숨긴다. 익명→등록처럼 같은 사람일 가능성이 있어도 새 session이면 같은
  규칙을 적용한다.

## Assistant turn 모델

polling 응답으로 서버가 소유할 최소 turn 상태를 정의한다.

| 필드 | 목적 |
| --- | --- |
| `turnId` | 음성·화면 응답과 tool 실행을 하나로 연결 |
| `sessionId` | turn 시작 시 사용자 session 또는 `null` |
| `profileId` | 개인화에 사용한 profile 또는 `null` |
| `phase` | progress 안내, tool 실행, final 응답 등 turn 내부 단계 |
| `sequence` | 늦거나 역순인 audio·화면 event 폐기 근거 |
| 상태 | `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED` |
| 화면 응답 | 제목·요약·상세 내용 또는 구조화된 결과 |
| 시각 | 시작·갱신·완료와 freshness |
| 오류 | 사용자에게 노출 가능한 실패 코드와 안내 |

Dashboard 전송은 `GET /api/assistant/latest` polling으로 확정한다. 서버는 현재 session에
표시할 최신 turn 하나 또는 `null`만 반환한다. SSE, WebSocket, 전체 대화 이력과 별도 event
broker는 이번 범위에 추가하지 않는다.

검색 전 진행 안내, tool 실행과 검색 후 최종 응답은 별도 대화가 아니라 같은 `turnId`의
연속 phase다. Voice와 Dashboard는 이 단위로 취소·완료를 판단하고, 사용자 session이 바뀌면
그 turn의 이후 phase와 늦은 event를 모두 폐기한다.

## 구현 단계

### Agents SDK 음성 전환

- [x] `openai-agents[voice]>=0.21,<0.22`, `openai>=3,<4`, `numpy>=2.2,<3`를 고정하고
  Python 3.14 개발 환경에서 `agents==0.21.1`, `openai==3.1.0`, `numpy==2.5.2` import와
  `AudioInput`/`SingleAgentVoiceWorkflow`/`VoicePipeline` API를 확인했다. Raspberry Pi native
  audio import는 배포 전 별도 검증이 필요하다.
  조합을 고정하고 x86·Raspberry Pi에서 import를 검증한다.
- [x] `AgentsVoiceRuntime`과 `SmartDeskVoiceWorkflow`를 만들고 model·STT·TTS·VAD 설정을
  불변 `AgentsVoiceConfig` 한 조립 경계에서 명시한다. runtime 입력은 24kHz mono PCM16
  `AsyncIterable`을 `StreamedAudioInput`에 직접 공급하며, 실행별 순번이 있는
  lifecycle/transcript/audio/error event만 외부로 낸다.
- [x] 첫 구현은 단일 Agent + `Runner` + 필요한 local function tool만 사용하고, handoff나
  다중 Agent orchestration을 추가하지 않는다.
- [x] microphone은 24kHz mono PCM16으로 한 번 capture하고, Wake Word 입력만 stateful
  resampler로 16kHz로 변환해 원본 24kHz chunk를 VoicePipeline에 전달한다.
- [x] final transcript 전에 Desk·WLED 같은 부작용 tool을 실행하지 않고, SDK hosted/function
  tool이 기존 public domain service만 호출하게 한다.
- [ ] 긴 tool 전 진행 안내와 최종 응답을 같은 Agent run·`turnId`에서 streaming TTS로
  재생하고 TTS 자체를 function tool로 만들지 않는다.
- [x] 일반 응답은 Wake Word 대기로 돌아가며 Agent가 `request_followup`을 호출한 경우에만
  TTS drain과 정책 재검증 뒤 제한된 follow-up 창을 연다.
- [x] 운영 Voice 경로를 `AgentsVoiceRuntime.run_audio` 하나로 전환하고 `OpenAiGateway`, 수동 Responses/tool loop, local RMS 발화 종료와 WAV STT
  경로를 제거하고 legacy/SDK 이중 실행 flag를 두지 않는다.
- [x] SDK lifecycle·audio·오류 event를 provider 중립 Voice runtime event로
  변환하고 OpenAI SDK 타입을 Dashboard·AutomationService에 노출하지 않는다.

### 현재 사용자 연결

- [x] `AgentsVoiceRuntime`에 current user snapshot adapter를 주입한다.
- [x] turn 시작 시 사용자 session을 캡처하고 context·tool·memory 동작에 전달한다.
- [x] session 변경 event로 이전 Agent run·대기 tool·TTS·follow-up을 취소하고 SDK session을 폐기한다.
- [x] 익명·session 없음·다중·교대 상태의 비개인화 동작과 memory 차단을 구현한다.
- [x] 익명 session 종료와 등록 전환에서 익명 짧은 history를 폐기한다.
- [x] 서버 재시작 또는 session 종료 후 이전 profile 문맥이 process state에 남지 않게 한다.

### 기억 경계

- [x] 책상 `sessionId`마다 Agents SDK memory session을 만들고 사용자 session 종료·전환과
  서버 재시작에서 폐기한다.
- [x] session history item cap으로 raw history 증가를 제한한다.
- [x] `profile:<profile_id>` namespace를 사용하는 memory service 경계를 구현한다.
- [x] Mem0 OSS를 `fin-main` process에 library로 포함하고 `data/mem0`를 명시적인 영속 경로로
  사용한다. Docker 전환 후에는 같은 container의 `/app/data/mem0` volume으로 연결한다.
- [x] 검색·저장할 정보, 최대 결과 수, timeout과 실패 fallback을 정한다.
- [x] turn 완료 시 같은 session인지 다시 확인한 뒤에만 사용자 기억을 저장한다.
- [x] profile 삭제 전에 장기 기억 전체 삭제를 완료하고 실패 시 profile DB를 보존한다.
- [ ] transcript, 사용자 ID와 기억 내용의 로그·보존·민감정보 범위를 문서화한다.

### Dashboard 응답

- [x] streaming Assistant 답변을 같은 turn의 FINAL 요약(최대 200자)과 필요 시 상세 응답으로 기록하며 raw transcript는 기록하지 않는다.
- [x] 현재 session의 최신 turn 하나를 반환하는 `/api/assistant/latest`와 Dashboard polling UI를 구현한다.
- [x] 늦게 완료된 과거 turn이 새 turn 화면을 덮어쓰지 않도록 `turnId`·sequence를 사용한다.
- [x] LISTENING → PROCESSING → 0개 이상의 TOOL → FINAL 순서로 같은 `turnId` phase를 발행한다.
- [x] session 교대·종료 시 이전 turn의 Dashboard 상세 응답을 즉시 숨긴다.
- [ ] tool 실행 중·성공·부분 실패를 음성과 화면에서 모순 없이 표현한다.

### tool 정책

- [x] WLED tool은 WLED public service만 호출하고 내부 client 상태를 우회하지 않게 유지한다.
- [x] Desk tool은 Agents SDK function tool로 구현하고 `AutomationService`의 session·mode·안전
  검증을 통한다.
- [x] `AutomationService.hold`와 `set_target`은 선택적 `expectedSessionId`를 받아 Voice turn의
  시작 session을 실제 Desk 부작용 직전까지 재검증한다. `None`은 기존 Dashboard의 신원 독립
  명령 경로이고, stale turn은 필요한 자동 이동 안전 STOP만 남기고 `SESSION_MISMATCH`로
  거절된다.
- [x] 작업 모드 선택 tool은 `activityModeKey`와 turn 시작 `expectedSessionId`로
  `AutomationService`를 호출하며 AUTO/MANUAL 보존 규칙을 따른다.
- [x] WLED 수동 변경 tool은 저장된 작업 모드를 수정하지 않고 현재 session override만 만든다.
- [x] tool 호출은 실제 `AutomationService` 호출 직전에 캡처한 session ID를 서버에서 재검증한다.
- [x] STOP 성격의 안전 명령과 개인화 명령의 권한·문맥 차이를 유지한다.

## 제외 범위

- 범용 채팅 서비스, 장기 대화 검색 UI와 여러 기기 동기화
- 얼굴을 인증 수단으로 사용하는 민감한 외부 계정 작업
- Dashboard가 current user나 Voice session을 직접 선택하는 기능
- Assistant가 `DeskController` 또는 relay를 직접 호출하는 구조

## 검증

- Wake Word는 16kHz 입력을 받고 VoicePipeline은 24kHz 원본을 받으며 장시간 resampling에서
  underrun이나 비정상 CPU 누적이 없다.
- partial transcript로 부작용 tool이 실행되지 않고, 설정한 VAD가 SDK 기본값과 무관하게
  적용된다.
- 일반 답변은 Wake Word로 복귀하고 `request_followup` turn만 후속 입력을 연다.
- A와 B profile의 기억이 서로 검색·저장되지 않는다.
- 익명 또는 current user가 없는 turn은 이전 사용자의 memory와 profile 설정을 사용하지 않는다.
- A session에서 시작해 B session에서 끝난 turn이 A나 B 기억에 잘못 저장되지 않는다.
- Dashboard의 editing profile 변경이 Voice 사용자와 memory namespace를 바꾸지 않는다.
- 늦은 과거 turn 응답이 최신 Dashboard 응답을 덮어쓰지 않는다.
- session 교대·종료 뒤 이전 turn이 완료돼도 이전 사용자의 상세 응답이 다시 나타나지 않는다.
- session 교대·종료가 이전 Agent run·미실행 부작용 tool·TTS·follow-up을 취소하고 이전 SDK
  session history를 폐기한다.
- 진행 안내, tool 상태와 최종 응답이 같은 `turnId`·순서로 보이고 취소 후 늦은 phase는
  재생·표시되지 않는다.
- session 없음·다중 상태의 임시 turn이 기존 SDK session과 Mem0를 읽거나 쓰지 않는다.
- 장기 기억 삭제 실패 시 profile·얼굴·작업 모드 DB가 삭제되지 않고 재시도할 수 있다.
- latest polling이 현재 session의 turn만 반환하고, 낮은 sequence와 이전 session turn을
  Dashboard가 적용하지 않는다.
- 같은 turn의 음성·화면 응답과 tool 결과가 일관된 성공·오류를 나타낸다.
- memory 또는 Dashboard delivery 장애가 Voice 기본 응답과 안전 STOP 처리를 막지 않는다.

## 완료 조건

- 운영 Voice 경로가 Agents SDK VoicePipeline 하나로 동작하고 legacy gateway·수동 tool loop가
  남아 있지 않다.
- Voice와 Dashboard AI 응답이 같은 서버 current user 근거와 `turnId`를 사용한다.
- 사용자 session 전환 시 이전 Agent 실행·출력·단기 대화 session이 새 사용자에게 넘어가지
  않는다.
- 사용자 없음·교대·불확실 상태에서 이전 사용자의 기억과 개인화가 재사용되지 않는다.
- profile별 기억의 저장·검색·삭제 및 로그 범위가 코드와 문서에 일치한다.
- Assistant의 장치 tool이 WLED·AutomationService의 공개 정책 경계를 우회하지 않는다.
