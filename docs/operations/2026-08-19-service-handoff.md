# 2026-08-19 Raspberry Pi 서비스 인수인계

## 목적과 현재 기준

이 문서는 Smart Desk의 Raspberry Pi 운영 서비스를 이어서 점검·수정할 작업자에게
현재 코드, 배포 상태, 접속 방법, 검증 증거와 남은 작업을 전달한다.

| 항목 | 현재 기준 |
| --- | --- |
| authoritative clone | `/srv/smart-desk-fin` |
| 운영 브랜치 | `main` |
| 배포된 코드 커밋 | `d7d4111` (`늦은 음성 전사 순서 처리`) |
| 직전 모델 변경 | `3be28f8` (`음성 모델 품질과 진단 강화`) |
| 디버그 화면 연결 | `e8d4ca8` (`음성과 비전 진단 화면을 연결`) |
| Raspberry Pi | `smarty@192.168.0.20` |
| Raspberry Pi 배포 경로 | `/srv/smart-desk-fin` |
| 배포 이미지 | `smart-desk-fin-main:local` / `sha256:c0d8fcc16820e506badb1aaa578108909db693d0b1f5d4484dc5408b9dc0921f` |
| Compose project/container | `smart-desk` / `smart-desk-main-1` |
| 작성 시점 상태 | Main `healthy`, restart 0 |

`/home/kimryungyo/smart-desk-fin` 등 다른 clone을 배포 기준으로 사용하지 않는다. 모든
병합, 커밋과 배포 소스는 `/srv/smart-desk-fin`의 `main`에서 진행한다. 현재 로컬 `main`은
GitHub `origin/main`보다 앞서 있으며 이 세션에서는 GitHub로 push하지 않았다. 사용자가
명시적으로 요청하기 전에는 원격 push를 배포의 일부로 간주하지 않는다.

`AGENTS.md`는 이 서버의 로컬 운영 메모이며 커밋 대상이 아니다. 운영 ESP32 경로는
Wi-Fi/MQTT이고 `feature/serial-esp32`은 비운영 보존 브랜치다.

## 이번 작업에서 병합·구현된 범위

로컬 `main`에는 Raspberry Pi 운영 변경과 Realtime 음성 기능이 병합돼 있다.

- `b949b17`: Raspberry Pi 운영 변경 병합
- `2605de5`: Realtime 음성 기능 병합
- `e8d4ca8`: 대시보드에 Vision/AI 스피커 진단 링크와 상태 패널 연결
- `3be28f8`: 최신 음성 모델 조합, 추론 설정과 상세 실패 로그 적용
- `d7d4111`: Realtime 응답 오디오보다 전사가 늦는 이벤트 순서 처리

현재 대시보드는 `AssistantPanel`, `VoiceStatusPanel`과 진단 도구 링크를 렌더링한다.
Vision 디버그 SPA 경로는 일반 curl의 `Accept` 값과 무관하게 열리도록 명시적 route가 있고,
음성 디버그 route는 같은 호스트의 10000번 포트로 보낸다.

## 접속과 서비스 URL

SSH private key 값은 문서에 복사하지 않는다. 배포 clone의 다음 경로를 사용한다.

```text
/srv/smart-desk-fin/.scratch/credentials/raspberry-pi-deploy-ed25519
```

```bash
cd /srv/smart-desk-fin
ssh -i .scratch/credentials/raspberry-pi-deploy-ed25519 \
  -o BatchMode=yes smarty@192.168.0.20
```

| 기능 | URL |
| --- | --- |
| 대시보드 | `http://192.168.0.20:9090/` |
| Vision 디버그 | `http://192.168.0.20:9090/debug/vision` |
| AI 스피커 디버그 | `http://192.168.0.20:10000/` |
| 음성 snapshot | `http://192.168.0.20:10000/api/snapshot` |
| Vision snapshot | `http://192.168.0.20:9090/api/vision/debug` |
| 상단 frame | `http://192.168.0.20:9090/api/vision/debug/frame/upper` |
| 하단 frame | `http://192.168.0.20:9090/api/vision/debug/frame/lower` |

작성 시점에 세 HTML route와 Vision 상·하단 JPEG API는 모두 HTTP 200이었다.

## Compose 운용

Compose service key는 대문자 컨테이너 이름이 아니라 소문자 `main`이다. Raspberry Pi의
일부 env 파일은 일반 사용자 읽기 권한이 없으므로 Compose와 Docker 명령은 `sudo -n`으로
실행한다.

```bash
cd /srv/smart-desk-fin
sudo -n docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  config --services
```

Main만 재빌드·교체할 때:

```bash
sudo -n docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  stop main

sudo -n docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  build main

sudo -n docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  up -d main
```

상태와 오류 확인:

```bash
sudo -n docker ps --filter name=smart-desk-main-1 \
  --format '{{.Names}}|{{.Image}}|{{.Status}}'
sudo -n docker inspect smart-desk-main-1 \
  --format 'restarts={{.RestartCount}} status={{.State.Status}}'
sudo -n docker logs --since 10m smart-desk-main-1 2>&1 \
  | grep -E 'ERROR|CRITICAL|episode_failed'
```

## 소스 동기화 주의사항

Raspberry Pi의 runtime 설정·데이터·secret·모델 파일을 덮어쓰지 않는다. rsync 시 최소한
다음을 보존한다.

```text
.env 및 백업본
data/
.scratch/
deploy/env/
deploy/mediamtx/mediamtx.runtime.yml
firmware/*/include/secrets.h
assets/vision/models/*.onnx 운영 provision 파일
.git/, .venv/, frontend/node_modules/, frontend/dist/
```

기존 배포에서 사용한 전체 exclude 명령은 현재 세션 history를 참고할 수 있지만, 다음
작업자는 실행 전 `rsync --dry-run` 또는 명시적인 exclude 목록으로 대상이 맞는지 다시
확인한다. `--delete`를 쓰더라도 위 runtime 파일은 반드시 exclude한다.

## AI 스피커 현재 구성

현재 기본 모델 조합은 다음과 같다. Raspberry Pi `.env`에 같은 이름의 override가 있으면
그 값이 우선하므로 배포 뒤 container 안의 `Settings()`로 effective value를 확인한다.

| 역할 | 설정 |
| --- | --- |
| speech-to-speech | `gpt-realtime-2.1` |
| 입력 전사 | `gpt-transcribe`, language `ko` |
| Realtime reasoning | `medium` |
| 복잡 요청 delegate | `gpt-5.6-sol`, reasoning `medium` |
| 출력 voice | `coral` |

ChatGPT Voice와 API Realtime은 동일 제품/모델이라고 가정하지 않는다. 위 조합은 공개 API의
Realtime voice agent용 구성이다. 복잡한 현재 정보·검색·긴 설명은
`delegate_complex_request` 도구로 GPT-5.6에 위임하고, 직접 장치 명령은 Realtime 모델이
제한된 로컬 도구를 호출한다.

## 마지막 Voice 장애와 수정 내용

### 관찰된 증상

사용자가 `하이 스마티` 뒤 발화를 끝내면 AI 응답 없이 바로 `WAITING_WAKE`로 돌아갔다.
배포 전 snapshot에는 `last_error=voice_pipeline_failed`가 남았고, 13:19 UTC 부근에 같은
패턴이 세 번 연속 발생했다.

### 원인

Realtime의 응답 오디오와 별도 입력 전사는 비동기다. `gpt-transcribe` 사용 시 실제로
`response.output_audio.delta`가 먼저 오고
`conversation.item.input_audio_transcription.completed`가 나중에 올 수 있다.

기존 `VoiceService`는 전사 전에 AUDIO event가 오면 즉시 fail-closed했고,
`RealtimeVoiceRuntime`도 `response.done`에서 전사가 아직 없더라도 socket을 닫았다. 이 두
가정 때문에 정상 응답이 재생되지 않고 wake-word 대기로 복귀했다.

### 적용한 수정

- AUDIO가 전사보다 먼저 와도 입력을 닫고 즉시 speaker 재생을 시작한다.
- `response.done` 뒤 전사가 없으면 최대 10초 동안 final transcript를 기다린다.
- 늦은 transcript가 오면 TRANSCRIPT 뒤 `TURN_ENDED`를 전달한다.
- grace 기간에도 transcript가 없을 때만 `voice_pipeline_failed`로 실패한다.
- `turn_end_waiting_for_transcript`, `late_transcript_received`, `episode_failed` 진단 event를
  content-free 로그로 남긴다.
- provider 오류에서는 type/code/param만 기록하고 transcript/message는 기록하지 않는다.

### 검증 증거

- 전체 pytest 통과, hardware opt-in 성격의 기존 3건만 skip
- `compileall`, `git diff --check` 통과
- 배포 컨테이너에서 합성한 실제 한국어 24kHz PCM을 OpenAI Realtime에 전송
- 실제 event 순서: AUDIO 3개 → TRANSCRIPT → 나머지 AUDIO → TURN_ENDED
- 총 AUDIO event 57개, ERROR event 0개, 최종 lifecycle 정상
- 배포 뒤 Voice snapshot: `WAITING_WAKE`, `last_error=null`

이 통합 검증은 실제 OpenAI endpoint와 배포 코드의 event ordering을 검증했지만, 수정 배포
뒤 사용자의 실제 목소리로 wake word부터 speaker drain까지 실행한 현장 검증은 아직 하지
못했다. 관찰 30초 동안 실제 wake word가 들어오지 않았기 때문이다.

## 다음 작업자의 최우선 확인

1. 사용자가 실제 위치에서 `하이 스마티` 후 짧은 질문을 한 번 말하도록 하고 다음 순서를
   확인한다.

   ```text
   WAITING_WAKE → RECORDING → PROCESSING 또는 SPEAKING → speaker drain → WAITING_WAKE
   ```

2. 같은 시각의 Main 로그에서 다음을 확인한다.

   ```bash
   sudo -n docker logs --since 5m smart-desk-main-1 2>&1 \
     | grep -E 'voice|assistant.realtime|episode_failed|ERROR|CRITICAL'
   ```

3. 늦은 순서였다면 아래 두 event가 함께 나오고 응답이 끝까지 재생돼야 한다.

   ```text
   turn_end_waiting_for_transcript
   late_transcript_received
   ```

4. Voice debug snapshot에서 `last_error=null`, assistant latest에서 `SUCCEEDED`, wakeword
   `armed=true`를 확인한다.

5. 최근 관찰에서 PortAudio `overflow_frames=2`가 있었지만 `dropped_frames=0`,
   `callback_errors=0`이었다. 이번 즉시 복귀의 직접 원인은 event ordering이었으나,
   overflow가 반복 증가하거나 인식률이 계속 나쁘면 Pi 부하, USB/PipeWire/ALSA capture
   buffer와 실제 microphone gain을 별도 측정한다. raw audio는 저장하지 않는다.

## 카메라와 관련 서비스 상태

작성 시점 검증 결과:

- Vision upper frame: JPEG, 기존 확인 크기 1920x1080
- Vision lower frame: JPEG, 기존 확인 크기 640x360
- USB user/workspace camera publisher 동작
- CSI publisher `rpi-camera-stream.service` 동작 및 MediaMTX `bottom-cam` publish 확인
- Vision worker의 upper/lower debug와 analyze endpoint 응답
- Desk height, relay heartbeat, tilt, WLED와 automation은 앞선 점검에서 online/ready

실제 책상 이동이나 relay 동작은 이 인수인계 검증을 이유로 자동 실행하지 않는다. 이동이
필요하면 `docs/operations/README.md`의 안전 조건과 현장 승인을 먼저 따른다.

## 롤백

보존된 주요 이미지 tag:

```text
smart-desk-fin-main:pre-late-transcript-3be28f8
smart-desk-fin-main:pre-voice-model-e8d4ca8
smart-desk-fin-main:pre-debug-links-e8d4ca8
smart-desk-fin-main:pre-main-2605de5
```

롤백 전 현재 컨테이너, tag와 대상 image ID를 읽기 전용으로 확인한다. 롤백은 장애가
확인됐을 때만 수행하고, 현재 이미지도 새 tag로 보존한다. 예시:

```bash
sudo -n docker tag smart-desk-fin-main:local \
  smart-desk-fin-main:before-manual-rollback
sudo -n docker tag smart-desk-fin-main:pre-late-transcript-3be28f8 \
  smart-desk-fin-main:local
sudo -n docker compose --env-file .env \
  -f deploy/compose.yml \
  -f deploy/compose.raspberry-pi.yml \
  up -d --force-recreate main
```

롤백 뒤에도 health, Voice debug, Vision frame과 Main 로그를 다시 검증한다.

## 완료 판단

다음 실제 발화가 AI 음성 응답과 speaker drain까지 완료되고, `last_error=null`, assistant
turn `SUCCEEDED`, Main restart 0을 함께 확인하면 이번 Voice 즉시 복귀 장애를 현장 완료로
판정할 수 있다. 이후에는 PortAudio overflow와 사용자 체감 인식률을 별도 품질 개선 작업으로
분리한다.
