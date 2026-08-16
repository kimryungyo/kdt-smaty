# 08. AI 사용자 문맥

Agents SDK 교체의 model, VoicePipeline, session adapter와 Mem0 배포 기준은
[Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)을 따른다.

## 사용자 결과

Voice는 등록 session에서만 해당 사용자의 기억과 profile 설정을 사용한다. 익명 session은
일반 대화와 session 범위의 짧은 history만 사용하고 장기 기억을 읽거나 저장하지 않는다.
다중·count 불일치에서는 이전 사람의 개인화를 일시 차단한다. 음성 답변과 함께 필요한 상세
정보는 같은 Assistant turn으로 Dashboard에 표시된다.

## 현재 상태

- wake word, 녹음, Assistant 호출, playback과 WLED function tool 기반이 구현돼 있다.
- 현재 수동 STT·Responses·TTS 경로는 Agents SDK `VoicePipeline`으로 교체하기로 확정됐지만
  아직 구현되지 않았다.
- Voice는 아직 서버 current user session을 입력으로 받지 않는다.
- profile별 장기 기억 service와 저장 정책은 설계 문서 수준이다.
- Dashboard에 Assistant turn이나 화면용 상세 응답을 전달하는 API가 없다.
- WLED tool은 있지만 Desk 사용자 명령 tool은 자동화 경계에 연결되지 않았다.

## 사용자 귀속 원칙

- Dashboard에서 편집한 profile은 Voice 사용자 문맥이 아니다.
- fresh하고 사용 가능한 current user session에서만 `profile:<profile_id>` 기억을 사용한다.
- 익명 session은 profile memory 없이 `sessionId` 범위의 짧은 history만 유지한다.
- Agents SDK session 객체는 사용자 session service가 소유하지 않는다. Voice memory adapter가
  책상 `sessionId`를 key로 생성·재사용·폐기한다.
- session 없음과 다중·count 불일치 중 일반 질문은 해당 Wake Word/follow-up 묶음에만 유효한
  임시 비개인화 session으로 처리한다.
- Assistant turn 시작 시 `sessionId`와 profile ID를 캡처한다.
- turn 완료 시 session이 바뀌거나 정책상 불확실해졌다면 이전 profile 장기 기억에 저장하지
  않는다.
- 화면 응답 delivery ID와 기억 user ID를 혼동하지 않는다.
- 익명 또는 session 없음에서도 일반 비개인화 질문을 허용하되 사용자별 기억은 사용하지 않는다.

## 삭제와 화면 노출 정책

- 장기 기억은 기존 설계대로 등록 사용자, `explicit_only`, raw transcript 비저장을 유지한다.
- profile 삭제는 `profile:<profile_id>` 장기 기억 전체 삭제를 먼저 완료한 뒤 얼굴·preset과
  profile row를 삭제한다. 기억 삭제 실패 시 profile DB는 유지하고 `503`으로 재시도를
  안내한다.
- current `sessionId`가 다른 값으로 바뀌거나 `null`이 되면 Dashboard에서 직전 session의 AI
  상세 응답을 즉시 숨긴다. 익명→등록처럼 같은 사람일 가능성이 있어도 새 session이면 같은
  규칙을 적용한다.

## Assistant turn 모델

구체적인 전송 방식 전에 서버가 소유할 최소 turn 상태를 정의한다.

| 필드 | 목적 |
| --- | --- |
| `turnId` | 음성·화면 응답과 tool 실행을 하나로 연결 |
| `sessionId` | turn 시작 시 사용자 session 또는 `null` |
| `profileId` | 개인화에 사용한 profile 또는 `null` |
| 상태 | listening, processing, responding, succeeded, failed 등 |
| 화면 응답 | 제목·요약·상세 내용 또는 구조화된 결과 |
| 시각 | 시작·갱신·완료와 freshness |
| 오류 | 사용자에게 노출 가능한 실패 코드와 안내 |

Dashboard 전송은 polling, SSE 또는 다른 단순 방식을 비교해 현재 단일 서버에 가장 작은
구조를 선택한다. 여러 과거 대화를 관리하는 chat 제품을 만들지 않고 최신 turn과 필요한
짧은 이력만 제공한다.

## 구현 단계

### 현재 사용자 연결

- [ ] VoiceService 또는 AssistantService가 current user snapshot을 안전하게 읽도록 주입한다.
- [ ] turn 시작 시 사용자 session을 캡처하고 transcript·tool·memory 동작에 전달한다.
- [ ] 익명·session 없음·다중·교대 상태의 비개인화 동작과 memory 차단을 구현한다.
- [ ] 익명 session 종료와 등록 전환에서 익명 짧은 history를 폐기한다.
- [ ] 서버 재시작 또는 session 종료 후 이전 profile 문맥이 process state에 남지 않게 한다.

### 기억 경계

- [ ] 책상 `sessionId`마다 Agents SDK memory session을 만들고 사용자 session 종료·전환과
  서버 재시작에서 폐기한다.
- [ ] 긴 책상 사용에서 raw history가 무제한 증가하지 않도록 item/token 제한 또는 compaction을
  설정화한다.
- [ ] `profile:<profile_id>` namespace를 사용하는 memory service 경계를 구현한다.
- [ ] Mem0 OSS를 `fin-main` process에 library로 포함하고 `data/mem0`를 명시적인 영속 경로로
  사용한다. Docker 전환 후에는 같은 container의 `/app/data/mem0` volume으로 연결한다.
- [ ] 검색·저장할 정보, 최대 결과 수, timeout과 실패 fallback을 정한다.
- [ ] turn 완료 시 같은 session인지 다시 확인한 뒤에만 사용자 기억을 저장한다.
- [ ] profile 삭제 전에 장기 기억 전체 삭제를 완료하고 실패 시 profile DB를 보존한다.
- [ ] transcript, 사용자 ID와 기억 내용의 로그·보존·민감정보 범위를 문서화한다.

### Dashboard 응답

- [ ] 음성 문장과 화면용 상세 응답을 하나의 Assistant 결과로 생성한다.
- [ ] 최신 turn snapshot 또는 event API와 Dashboard 표시를 구현한다.
- [ ] 늦게 완료된 과거 turn이 새 turn 화면을 덮어쓰지 않도록 `turnId`·sequence를 사용한다.
- [ ] session 교대·종료 시 이전 turn의 Dashboard 상세 응답을 즉시 숨긴다.
- [ ] tool 실행 중·성공·부분 실패를 음성과 화면에서 모순 없이 표현한다.

### tool 정책

- [ ] WLED tool은 WLED public service만 호출하고 내부 client 상태를 우회하지 않게 유지한다.
- [ ] Desk tool을 추가한다면 `AutomationService`의 session·mode·안전 검증을 반드시 통한다.
- [ ] tool 호출이 사용자 교대와 경합하면 캡처한 session ID를 서버에서 재검증한다.
- [ ] STOP 성격의 안전 명령과 개인화 명령의 권한·문맥 차이를 유지한다.

## 제외 범위

- 범용 채팅 서비스, 장기 대화 검색 UI와 여러 기기 동기화
- 얼굴을 인증 수단으로 사용하는 민감한 외부 계정 작업
- Dashboard가 current user나 Voice session을 직접 선택하는 기능
- Assistant가 `DeskController` 또는 relay를 직접 호출하는 구조

## 검증

- A와 B profile의 기억이 서로 검색·저장되지 않는다.
- 익명 또는 current user가 없는 turn은 이전 사용자의 memory와 profile 설정을 사용하지 않는다.
- A session에서 시작해 B session에서 끝난 turn이 A나 B 기억에 잘못 저장되지 않는다.
- Dashboard의 editing profile 변경이 Voice 사용자와 memory namespace를 바꾸지 않는다.
- 늦은 과거 turn 응답이 최신 Dashboard 응답을 덮어쓰지 않는다.
- session 교대·종료 뒤 이전 turn이 완료돼도 이전 사용자의 상세 응답이 다시 나타나지 않는다.
- 장기 기억 삭제 실패 시 profile·얼굴·preset DB가 삭제되지 않고 재시도할 수 있다.
- 같은 turn의 음성·화면 응답과 tool 결과가 일관된 성공·오류를 나타낸다.
- memory 또는 Dashboard delivery 장애가 Voice 기본 응답과 안전 STOP 처리를 막지 않는다.

## 완료 조건

- Voice와 Dashboard AI 응답이 같은 서버 current user 근거와 `turnId`를 사용한다.
- 사용자 없음·교대·불확실 상태에서 이전 사용자의 기억과 개인화가 재사용되지 않는다.
- profile별 기억의 저장·검색·삭제 및 로그 범위가 코드와 문서에 일치한다.
- Assistant의 장치 tool이 WLED·AutomationService의 공개 정책 경계를 우회하지 않는다.
