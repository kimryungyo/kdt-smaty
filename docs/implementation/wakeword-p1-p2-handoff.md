# Wake Word P1 신호 관측·P2 마이크 교정 구현 인수인계

## 문서 목적

이 문서는 `smart-desk-fin`의 `하이 스마티` Wake Word 인식률 저하를 조사하기 위해
P1 입력 신호 관측과 P2 AKG Ara microphone gain 교정을 구현·수행할 다음 AI 작업자에게
현재 상태, 확정 범위, 구현 계약, 사용자 협업 지점과 완료 조건을 전달한다.

> 2026-08-16 `feat/agents-sdk-voice-pipeline`에서 microphone 입력 계약을
> 24kHz/1,920 samples/80ms로 변경했다. 아래 16kHz 수치와 측정값은 P1·P2 당시 기준선이며,
> 현재 Wake Word 경로는 2초 입력 창을 추론 직전에 16kHz로 변환한다.

이 작업의 목표는 아직 noise suppression을 도입하는 것이 아니다. 먼저 raw PCM을
저장하지 않는 content-free 계측으로 입력 품질을 숫자로 확인하고, 대상 microphone의
gain을 실제 사용자의 거리·음량에 맞춘다. 이 결과가 있어야 다음 단계에서 threshold,
inference cadence, high-pass filter 또는 noise suppressor를 근거 있게 비교할 수 있다.

| 항목 | 기준 |
| --- | --- |
| 작성 기준일 | 2026-08-15 |
| 기준 브랜치 | `feat/ai-speaker-debug` |
| 기준 커밋 | `5c27f73` |
| 프로젝트 경로 | `/srv/smart-desk-fin` |
| 대상 microphone | AKG Ara USB Microphone, USB ID `0ecb:2057` |
| 당시 입력 계약 | 16kHz, mono, signed PCM16 little-endian, 1,280 samples/80ms |
| Wake Word model | `hi_smarty_ko_synthetic_v0_1_0.onnx` |

이 문서는 Wake Word 관측·교정 작업의 범위와 기준선만 다룬다. 다음 작업자는 실제
작업을 시작하기 전에 `git status --short`로 별도의 사용자 변경이 있는지 확인하고,
관련 없는 파일은 수정하거나 구현 commit에 포함하지 않는다.

## 사용자와의 역할 분담

대부분의 작업은 다음 AI가 수행할 수 있다. 사용자의 필수 참여는 실제 소리를 제공하거나
물리 장치를 만져야 하는 구간에 한정한다.

| 단계 | 다음 AI | 사용자 |
| --- | --- | --- |
| P1 설계·구현·자동 테스트 | 전부 수행 | 없음 |
| 디버그 API/UI에서 수치 확인 | 전부 수행 | 없음 |
| microphone 무음 기준 측정 | 측정 시작·결과 분석 | 안내 시간 동안 조용히 있기 |
| 실제 발화 측정 | 시나리오 진행·수치 수집·판정 | 평소 위치에서 `하이 스마티` 발음 |
| software capture gain 변경 | 현재값 백업·단계 조정·read-back 확인 | 변경 허용 및 소리 재시험 |
| 물리 microphone 조정 | 방향·거리·노브 조정 안내 | microphone을 직접 이동하거나 노브 조정 |
| 최종값 선택 | 결과 비교·권고 | 실제 사용감 확인 |
| 부팅 후 영구 적용 | 방법 제안·승인 후 적용 | 시스템 변경 승인 |

사용자가 자리를 비운 상태에서는 P1 자동 테스트까지 진행할 수 있다. P2 최종 교정은
사용자의 목소리와 실제 사용 위치가 ground truth이므로 완전 자동화하지 않는다.

## 현재 확인된 기준선

### 실행 설정

2026-08-15 실행 중인 `.env`와 debug snapshot에서 다음을 확인했다.

```text
VOICE enabled                         true
input device                          AKG Ara mono-fallback PipeWire source
wakeword threshold                    0.35
wakeword consecutive frames           1
wakeword inference interval frames    5 (400ms)
input queue                           64 frames
debug server                          0.0.0.0:10000
```

모델 문서의 provisional setting은 threshold `0.13`, consecutive evaluations `2`다.
현재 운영값 `0.35/1`은 이 권고와 다르다. 다만 P1·P2에서는 gain과 관측 효과를 분리하기
위해 threshold/cadence를 동시에 바꾸지 않는다. 설정 A/B는 별도 단계로 수행한다.

### 실제 장치와 runtime

확인 당시 상태는 다음과 같다.

- AKG Ara source는 정상 연결됐고 Voice는 `WAITING_WAKE`였다.
- queue drop, PortAudio overflow, callback error는 모두 0이었다.
- ALSA `Mic` capture는 `47/47`, `100%`, `+32.00dB`였다.
- PipeWire source volume은 `1.00`이었다.
- 물리 source node는 48kHz이고 PortAudio stream은 `1280/16000`이므로 PipeWire 경로에서
  16kHz로 변환된다.
- 8초 무음 상태에서 debug score 80회는 min `0.003803`, mean `0.005482`, max
  `0.010357`이었다. 이는 그 순간의 조용한 환경만 나타내며 키보드·fan·주변 대화나
  실제 발화 품질을 증명하지 않는다.
- 전체 Uvicorn process는 짧은 3초 표본에서 CPU `125~211%`를 사용했다. Voice 외 Vision
  등도 같은 process에 있으므로 Wake Word 비용으로 단정하지 않는다.

안정적인 ALSA card ID는 숫자 `2`가 아니라 `Microphone`이다. read-only 확인은 다음처럼
가능하다.

```bash
amixer -c Microphone sget Mic
```

card 번호와 PipeWire node 숫자는 hot-plug 또는 재부팅 뒤 바뀔 수 있으므로 자동화에
고정하지 않는다.

### 코드상 원인과 관측 공백

- `LocalAudioInput` callback은 PCM bytes와 timestamp만 queue로 넘긴다.
- 현재 `AudioInputDebugSnapshot`에는 accepting, queue size/capacity, drop, overflow,
  callback error만 있다.
- 이 historical handoff의 `RmsRecorder` 발화 종료 설명은 superseded다. 현재 RMS는 follow-up 후보의
  네트워크 실행 회피에만 쓰고 final speech는 SDK server VAD가 정한다.
- peak, clipping, DC offset, noise floor, SNR을 계산하지 않는다.
- `WakeWordDebugSnapshot`에는 마지막 score만 있고 최근 최대 score와 inference latency가
  없다.
- 실제 hardware test는 microphone open과 acknowledgement 재생만 검사한다.
- bundled model integration test는 무음 PCM으로 load/infer/reset/close만 확인한다.

관련 파일:

```text
src/smart_desk/modules/voice/models.py
src/smart_desk/modules/voice/audio.py
src/smart_desk/modules/voice/wakeword.py
src/smart_desk/modules/voice/debug.py
src/smart_desk/modules/voice/service.py
src/smart_desk/bootstrap.py
tests/unit/test_voice_audio.py
tests/unit/test_voice_debug.py
tests/unit/test_wakeword_detector.py
tests/integration/test_voice_hardware.py
tests/integration/test_wakeword_builtin.py
```

## 확정 범위

### P1 포함 범위

1. 각 입력 frame의 RMS dBFS, peak dBFS, clipping ratio와 DC offset 계산
2. 최근 입력의 추정 noise floor와 현재 frame의 추정 SNR 계산
3. signal 통계를 `AudioInputDebugSnapshot`과 debug page에 노출
4. Wake Word inference latency와 최근 score 최대값을 content-free snapshot으로 노출
5. pure 계산, rolling state, JSON/API와 HTML 표시의 자동 테스트
6. raw PCM, WAV와 발화 내용을 저장하지 않는 개인정보 경계 유지

### P2 포함 범위

1. stable ALSA card ID로 현재 capture gain과 범위를 다시 확인
2. 사용자 참여형 무음·평상 발화·근거리 큰 발화 측정
3. software capture gain을 작은 단계로 조정하고 매 단계 read-back
4. recognition count, peak, clipping, noise floor와 SNR을 함께 비교
5. 선택값, 측정 조건과 되돌리기 값을 결과 문서에 기록
6. 실제 장치 검증 뒤 필요할 때만 영구 적용안을 별도 승인받아 수행

### 제외 범위

다음 항목을 P1·P2에 섞지 않는다.

- WebRTC NS, RNNoise, SpeexDSP 등 noise suppression 도입
- DC blocker/high-pass filter를 실제 audio에 적용
- AGC, compressor, limiter 적용
- model 재학습 또는 ONNX 교체
- threshold/consecutive/inference interval 변경
- STT 녹음 정책과 RMS speech detector 변경
- acknowledgment 재생과 one-shot 발화 UX 변경
- raw audio 상시 저장, debug API를 통한 audio 제공
- debug server 인증 또는 전체 frontend 재설계

P1 결과에서 clipping이 확인되더라도 P2 gain 교정을 먼저 수행한다. noise floor가 높다는
이유만으로 강한 denoiser를 바로 추가하지 않는다.

## P1 상세 구현 계약

### 1. 순수 frame 분석 함수

`audio.py`에 immutable signal DTO와 pure 분석 함수를 두는 안을 권장한다. 정확한 이름은
현재 naming과 테스트 가독성에 맞춰 정할 수 있지만 책임을 `LocalAudioInput` 내부에만
숨기지 않는다.

후보 필드:

```python
@dataclass(frozen=True, slots=True)
class AudioSignalFrame:
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    dc_offset_pcm: float
```

계산 규칙:

```text
samples64       = PCM16을 float64 또는 int32로 확장
rms             = sqrt(mean(samples64 ** 2))
peak            = max(abs(samples64))
rms_dbfs        = 20 * log10(rms / 32768)
peak_dbfs       = 20 * log10(peak / 32768)
clipping_ratio  = count(abs(sample) >= 32760) / 1280
dc_offset_pcm   = mean(samples64)
```

- `-32768`의 abs overflow를 피하기 위해 int16 상태에서 절댓값을 계산하지 않는다.
- 0은 `-inf`나 NaN 대신 `-120.0 dBFS` 같은 문서화된 finite floor로 clamp한다.
- dBFS는 `[-120, 0]` 범위로 제한한다.
- clipping threshold `32760`은 양·음 rail에 거의 붙은 sample을 잡기 위한 진단값이다.
  실제 DSP limiter threshold로 재사용하지 않는다.
- 모든 snapshot 값은 JSON 직렬화 가능한 finite 값 또는 입력 전의 `None`이어야 한다.

기존 `calculate_rms()`의 결과 계약은 recorder가 사용하므로 임의로 dBFS 반환으로 바꾸지
않는다. 공통 내부 계산을 재사용하더라도 공개 동작과 테스트를 보존한다.

### 2. rolling signal 상태

`LocalAudioInput`이 최근 accepted PCM frame의 content-free 통계만 소유한다.

권장 초기 계약:

```text
frame duration                  0.08s
noise window                    30s / 375 frames
recent peak window              10s / 125 frames
noise floor                     rolling RMS dBFS의 20th percentile
estimated SNR                   latest RMS dBFS - noise floor dBFS
clipped frame total             clipping_ratio > 0인 frame의 누적 개수
signal frame total              분석에 성공한 frame 누적 개수
```

noise floor와 SNR은 별도 VAD가 없는 추정치임을 코드·UI에서 명확히 표시한다. 30초 대부분이
발화인 경우 noise floor가 상승할 수 있으며 이를 정밀 acoustic calibration으로 표현하지
않는다.

`AudioInputDebugSnapshot` 후보 확장:

```python
latest_rms_dbfs: float | None
latest_peak_dbfs: float | None
recent_peak_dbfs: float | None
estimated_noise_floor_dbfs: float | None
estimated_snr_db: float | None
latest_clipping_ratio: float | None
clipped_frames: int
signal_frames: int
latest_dc_offset_pcm: float | None
```

명칭은 최종 구현에서 줄일 수 있지만 최소한 RMS, peak, noise floor, SNR, clipping과 DC
offset을 서로 구분할 수 있어야 한다.

### 3. thread와 성능 경계

PortAudio callback은 지금처럼 다음만 수행한다.

```text
PCM ownership copy → monotonic timestamp → event loop enqueue 예약
```

NumPy 변환, percentile, log10이나 rolling deque 갱신을 `_callback()`에서 수행하지 않는다.
PCM 길이와 generation을 검증한 뒤 event-loop-owned `_enqueue_from_loop()` 또는 별도
event-loop helper에서 frame 통계를 갱신한다. 1,280 sample/80ms의 계산은 작지만 callback
deadline과 분리하는 원칙을 보존한다.

percentile은 매 frame 375개 전체 정렬을 해도 작지만, 구현 뒤 event-loop 지연과 전체 CPU를
실측한다. 불필요한 executor 호출이나 새 background task는 추가하지 않는다.

통계 state는 다음 원칙을 권장한다.

- audio stream start에서 rolling history와 counters 초기화
- `discard_pending()`은 queue generation만 바꾸고 signal history는 지우지 않음
- playback 때문에 `accepting=False`인 frame은 기존 정책대로 계측 대상에서 제외
- stop 뒤 snapshot은 마지막 값 대신 `None`으로 초기화하여 stale 값을 현재값처럼 보이지 않게
  함

### 4. Wake Word score와 inference latency

`LiveKitWakeWordOnnxDetector.detect()`에서 실제 `_infer` 실행 구간만 `time.perf_counter()`로
측정한다. 2초 warm-up과 inference interval로 skip된 frame은 latency 표본에 넣지 않는다.

`WakeWordDebugSnapshot` 후보 확장:

```python
recent_max_score: float | None
inference_count: int
last_inference_ms: float | None
inference_p50_ms: float | None
inference_p95_ms: float | None
```

- 최근 score 최대값은 time-stamped 30초 rolling window를 권장한다.
- latency percentile은 최근 성공한 256회처럼 bounded sample로 계산한다.
- inference failure는 기존 `wakeword_inference_failed` fatal 경로를 보존한다.
- raw samples, embeddings와 per-frame audio content는 snapshot이나 log에 넣지 않는다.
- `reset()` 후 마지막 score를 유지하는 기존 의미가 있으므로 바꿀 경우 기존 UI와 테스트의
  의도를 먼저 확인한다. 새 `recent_max_score`의 reset 의미는 테스트로 고정한다.

현재 full mel+embedding+classifier의 과거 benchmark는 같은 Ryzen 5 6600H에서 p50
약 `22.0ms`, p95 `31.2ms`, max `41.2ms`였다. P1 실측값이 이와 크게 다르면 inference
cadence를 바꾸기 전에 원인을 조사한다.

### 5. Debug API와 페이지

기존 `/api/snapshot`의 `audio_input`과 `wakeword` 객체를 확장한다. endpoint를 추가할
필요는 없다. API consumer가 repository 내부 debug page뿐이더라도 기존 key를 삭제하지
않는다.

페이지에는 최소 다음 값을 읽기 쉬운 단위로 표시한다.

```text
Input RMS             -24.1 dBFS
Input peak             -8.2 dBFS
Recent peak            -6.9 dBFS
Noise floor (est.)    -47.3 dBFS
SNR (est.)             23.2 dB
Clipping               0.000% / clipped frames 0
DC offset                12.4 PCM
Wake recent max         0.271
Inference              last 25.8ms / p50 22.3ms / p95 31.6ms
```

- 아직 입력 표본이 없으면 `--`로 표시한다.
- dBFS와 percentage formatting은 frontend JS에서 하되 API는 number/`null`을 반환한다.
- `estimated` 값을 확정 noise/SNR처럼 표현하지 않는다.
- clipping이 0보다 크면 색상 경고를 줄 수 있지만 임의의 자동 gain 변경은 하지 않는다.
- 현재 debug page는 50ms polling을 사용하므로 backend에 별도 push/event stream을 추가하지
  않는다.
- debug server는 현재 `0.0.0.0:10000`이고 인증이 없다. gain 변경, calibration start,
  microphone 제어 같은 write endpoint를 추가하지 않는다.

### 6. P1 자동 테스트

최소 단위 테스트:

1. full silence가 finite floor, peak와 clipping 0을 반환
2. `+1000/-1000` 대칭 sample의 RMS/peak와 DC offset 0
3. constant positive sample의 예상 DC offset
4. `-32768`에서 overflow 없이 peak 0dBFS 근처와 clipping 검출
5. 일부 rail sample에서 clipping ratio 정확성
6. rolling 20th percentile noise floor와 recent peak window 경계
7. start/stop/reset 시 snapshot state 의미
8. queue drop 동작이 signal 계측 추가 뒤에도 기존대로 동작
9. Wake Word fake inference의 last/p50/p95와 recent max score
10. debug API JSON key, finite/`null` 값과 page label/formatting

기존 test fixture가 `AudioInputDebugSnapshot`과 `WakeWordDebugSnapshot`을 직접 생성하므로
모든 호출부를 갱신한다.

P1 검증 명령:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_voice_audio.py \
  tests/unit/test_wakeword_detector.py \
  tests/unit/test_voice_debug.py

.venv/bin/python -m pytest
```

기존 worktree 변경과 무관한 실패가 있으면 원인과 범위를 보고하고 임의로 고치지 않는다.

## P2 상세 교정 절차

### 1. 선행 조건

다음을 모두 만족한 뒤 gain을 바꾼다.

- P1 자동 테스트 통과
- 실행 중 debug snapshot에서 signal 값이 갱신됨
- queue drop/overflow/callback error가 증가하지 않음
- 현재 ALSA capture 값과 restore command 기록
- 사용자가 교정 시작을 인지하고 발음 테스트에 참여 가능
- 주변 사람의 음성이 의도치 않게 분석되지 않는 환경

P1은 raw PCM을 저장하지 않지만 microphone을 실시간 분석한다. 사용자에게 측정 시작과
종료를 분명하게 알린다.

### 2. calibration 실행 방식

첫 구현에서는 외부에 열린 debug HTTP 서버에 write endpoint를 만들지 않는다. 다음 두
방식 중 단순한 쪽으로 진행한다.

#### 기본 방식: 실행 중 debug snapshot 관측

현재 Voice process를 유지하고 `/api/snapshot` 또는 debug page에서 수치를 읽는다.
장점은 microphone ownership 충돌이 없다는 점이다. 단점은 Wake Word가 감지될 때 전체
Voice turn과 OpenAI 흐름이 시작되어 반복 교정이 느릴 수 있다는 점이다.

#### 반복 시험이 필요할 때: local detector-only calibration CLI

반복 측정이 불편하면 local-only CLI를 추가할 수 있다. 이 도구는 기존 Voice process와
동시에 microphone을 열지 않고 다음만 수행한다.

```text
LocalAudioInput → P1 signal analyzer → LiveKitWakeWordOnnxDetector → terminal summary
```

계약:

- OpenAI, Assistant, TTS와 speaker를 시작하지 않음
- raw PCM/WAV/embedding을 file이나 stdout에 기록하지 않음
- 각 attempt 전에 2초 rolling window warm-up을 보장
- attempt별 max score, detected 여부, peak, noise floor, SNR과 clipping만 출력
- Ctrl-C에서 stream과 detector를 정상 close
- 장치 점유 실패를 content-free 오류로 보고
- `.env`의 device/model/threshold/cadence를 재사용하거나 명시적 인자로 주입하되 secret을
  출력하지 않음

CLI 추가가 P2를 과도하게 키우면 먼저 기본 방식으로 1회 교정한다. 반복 가능성 없이 범용
calibration framework, database나 새 HTTP API를 만들지 않는다.

### 3. 기준 측정

gain을 바꾸기 전에 동일 조건에서 baseline을 남긴다.

1. microphone 위치와 사용자의 입까지 대략적인 거리 기록
2. AKG Ara의 물리 방향/패턴과 software capture 값 기록
3. 15초 동안 조용히 있어 noise floor와 quiet peak 측정
4. 사용자가 평소 목소리로 `하이 스마티`를 10회 발음
5. 발음 사이에는 최소 2초 이상 두어 detector가 정상 대기하도록 함
6. 평소보다 가까운 거리에서 큰 목소리로 3회 발음해 clipping 확인
7. 가능하면 키보드 타이핑 30초를 negative screening으로 측정

10회 표본은 gain 조정을 위한 빠른 screening이지 최종 recall 통계가 아니다. 시스템은
감지된 호출만 자동으로 알 수 있으므로 사용자가 실제로 몇 번 발음했는지 attempt count를
명시적으로 알려주거나 calibration CLI가 정해진 attempt window를 제공해야 한다.

### 4. gain 변경

현재 stable card ID 기준 read/write 후보 명령:

```bash
amixer -c Microphone sget Mic
amixer -c Microphone sset Mic Capture 90%
amixer -c Microphone sget Mic
```

주의사항:

- 위 `90%`는 예시일 뿐 권고 시작값이 아니다.
- 먼저 현재 raw control과 dB 값을 결과 기록에 남긴다.
- 한 번에 큰 폭으로 낮추지 말고 5~10 percentage point 정도의 작은 단계로 조정한다.
- 각 변경 직후 `sget`으로 실제 raw/dB 값을 확인한다.
- ALSA control 이름이나 capability가 달라졌으면 command를 실행하지 말고 다시 조사한다.
- `wpctl` node 숫자나 ALSA card 숫자를 영구 설정에 사용하지 않는다.
- 물리 gain knob를 사용자가 움직이면 software read-back과 P1 수치를 다시 확인한다.
- microphone 위치·방향과 gain을 동시에 바꾸지 않는다. 한 변수씩 비교한다.

현재 확인된 원래 값은 Capture `47/47`, `100%`, `+32.00dB`지만 실제 변경 직전에 반드시
재조회한다. restore command는 그때 읽은 값으로 만든다.

### 5. 판정 기준

다음 값은 초기 교정용 heuristic이며 실제 recognition 결과보다 우선하지 않는다.

```text
normal speech peak            대략 -12 ~ -6 dBFS
close/loud clipping ratio     0% 목표
normal speech estimated SNR   가능하면 15dB 이상
quick recognition screening   10회 중 최소 9회
queue drop/overflow           증가 없음
```

- normal peak가 너무 낮고 SNR도 낮으면 gain을 올리거나 거리/방향을 개선한다.
- peak가 0dBFS에 붙거나 clipping이 있으면 gain을 낮춘다.
- noise floor와 speech가 함께 같은 폭으로 움직이면 gain만으로 SNR은 개선되지 않는다.
  microphone 위치, 방향과 진동 전달을 먼저 개선한다.
- signal 수치는 양호하지만 score만 낮으면 gain보다 model domain mismatch, threshold 또는
  inference cadence 문제다. P2를 끝내고 별도 A/B 단계로 넘긴다.
- 낮은 gain에서 recognition이 악화되면 임의의 목표 dBFS를 고집하지 말고 이전 최선값으로
  복구한다.

### 6. 물리 조정이 필요한 경우

다음은 사용자가 직접 해야 한다.

- microphone 수음 정면을 사용자 방향으로 맞추기
- 키보드 충격과 책상 진동이 직접 전달되는 위치 피하기
- fan 바람이 capsule에 직접 닿지 않게 하기
- 실제 사용하는 거리로 고정하기
- AKG Ara의 물리 gain/지향성 control이 있다면 안내에 따라 한 단계씩 조정하기

다음 AI는 물리 control의 현재 위치를 화면만 보고 추정하지 않는다. 사용자의 확인을 받고
한 변수씩 바꾼 뒤 P1 수치와 recognition을 다시 측정한다.

### 7. 영구 적용과 복구

ALSA mixer 값은 hot-plug·로그인·재부팅 뒤 유지된다고 가정하지 않는다. 먼저 선택값으로
한 세션을 검증하고 재연결 후 유지 여부를 read-only로 확인한다.

부팅 시 mixer 값을 강제하거나 `alsactl store` 같은 시스템 변경이 필요하면 다음을 문서화한
뒤 사용자 승인을 별도로 받는다.

- 적용 주체와 실행 사용자
- stable device 식별 방식
- microphone이 없을 때 실패 처리
- hot-plug 뒤 재적용 방식
- 원래 값 복구 명령
- 다른 애플리케이션의 microphone 사용 영향

P2 요청만으로 system-wide persistent audio state 변경 권한을 확대 해석하지 않는다.

## 결과 기록 형식

실제 교정 뒤 `docs/implementation/wakeword-calibration-results.md` 같은 별도 결과 문서를
만들거나 사용자가 지정한 작업 기록에 다음을 남긴다. raw audio와 transcript는 넣지 않는다.

```text
date/time and room condition
git commit and model hash
input device stable name
physical distance/orientation/pattern
ALSA capture raw/percent/dB before and after
PipeWire source volume
threshold/consecutive/inference interval (unchanged during P2)
quiet noise floor and peak
normal speech peak/SNR/clipping
close/loud clipping
attempted/detected count
keyboard/fan negative screening result
queue drop/overflow/callback errors
inference p50/p95
selected value and restore command
user-observed usability
```

측정 조건이 없는 숫자는 재현할 수 없으므로 최종값만 `.env`에 남기고 근거를 버리지 않는다.

## 권장 작업 순서

1. 시작 시 `AGENTS.md`, worktree 상태와 Voice 관련 코드·테스트 재확인
2. P1 pure signal analyzer와 단위 테스트 구현
3. `LocalAudioInput` rolling state와 snapshot 확장
4. Wake Word score/latency rolling telemetry와 테스트 구현
5. debug API fixture와 page 갱신
6. 관련 unit test와 전체 pytest 실행
7. 실제 서버를 안전하게 재시작할 권한과 현재 작업 흐름 확인
8. debug snapshot에서 P1 값과 기존 queue 상태 확인
9. 사용자에게 P2 측정 시작을 알리고 baseline 수행
10. current mixer 값을 기록하고 작은 단계로 gain 조정
11. 각 단계마다 동일 발화/무음 시나리오 반복
12. 최선값 선택, restore 가능성 확인과 결과 기록
13. 필요하면 hot-plug/reboot 지속성은 별도 승인 후 검증
14. P1·P2 완료 보고에서 다음 단계와 섞지 않고 결과만 전달

## 완료 조건

### P1 완료

- raw audio를 저장하지 않고 RMS/peak/noise/SNR/clipping/DC 수치를 실시간 확인할 수 있다.
- Wake Word 최근 score 최대와 inference last/p50/p95를 확인할 수 있다.
- 기존 queue, detector, Voice state 동작이 유지된다.
- 입력 전·stop 뒤 값이 stale current signal처럼 표시되지 않는다.
- 관련 단위 테스트와 전체 test suite가 통과한다.
- debug API에 secret, PCM, embedding이나 새 음성 content가 노출되지 않는다.

### P2 완료

- 변경 전 mixer 값을 기록했고 즉시 복구할 수 있다.
- 사용자 실제 위치에서 quiet/normal/close-loud 측정을 수행했다.
- 선택 gain에서 clipping이 없고 signal과 quick recognition이 baseline보다 같거나 개선됐다.
- queue drop/overflow나 Voice 장치 오류가 새로 발생하지 않았다.
- 사용자가 실제 체감 동작을 확인했다.
- 결과와 조건을 content-free 문서로 남겼다.
- persistent 적용을 하지 않았거나, 필요 시 별도 사용자 승인을 받아 적용·복구 방법을
  문서화했다.

## P1·P2 이후의 의사결정

P2 뒤에도 미탐이 남으면 같은 실제 발화 조건에서 다음을 별도 실험한다.

1. 모델 권고값 `threshold=0.13`, `consecutive=2`, `interval=5`
2. 부족할 경우 `interval=2`로 낮추고 CPU·latency·recall 비교
3. signal은 양호하지만 실제 음성 score 분포가 낮으면 target-device real data를 포함해
   model 재학습
4. noise floor가 높고 위치·gain으로 해결되지 않을 때만 high-pass/noise suppression 후보를
   offline raw-consented fixture에서 비교

denoiser를 도입하면 runtime audio만 바꾸지 말고 동일 preprocessing을 학습·validation에도
적용한다. 강한 suppression이 `하이 스마티`의 자음과 고주파 특징을 훼손할 수 있으므로
인식률과 false activation/hour를 함께 검증한다.

## 참조

- `README.md`의 AI 스피커 실행과 debug page 절
- `assets/voice/models/README.md`
- `docs/third-party/voice.md`
- `docs/architecture/ai-voice-assistant.md`
- `/srv/wakeword/artifacts/releases/hi-smarty-ko-synthetic-v0.1.0/MODEL_CARD.md`
- `deploy/udev/README.md`

현재 모델은 실제 사용자나 AKG Ara 데이터가 없는 합성 기준선이다. P1·P2는 신호 경로와
gain 문제를 제거하거나 증명하는 단계이며, 모델의 운영 품질 자체를 보장하는 단계가 아니다.
