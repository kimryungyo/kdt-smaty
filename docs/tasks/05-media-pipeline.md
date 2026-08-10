# 05. 카메라 발행과 최신 프레임

## 목표

FastAPI가 두 물리 웹캠을 카메라별 FFmpeg 자식 process로 열어 호스트에서 이미
실행 중인 MediaMTX에 발행한다. 카메라별 RTSP reader는 MediaMTX를 계속 읽으며
Task 06이 사용할 최신 프레임 하나만 메모리에 유지한다.

## 확정 데이터 흐름

```text
USB webcam → CameraPublisher → FFmpeg(Popen) → RTSP → host MediaMTX
                                                        ↓ RTSP
                                                  RtspFrameSource
                                                        ↓
                                                  최신 frame 하나
```

사용자 카메라와 자세 카메라마다 `CameraPublisher`와 `RtspFrameSource` 인스턴스를
하나씩 생성한다.

## 작업 목록

- [ ] 두 카메라의 안정적인 `/dev/v4l/by-id/...` 경로와 capture index를 확정한다.
- [ ] 지원 입력 format·해상도·FPS를 실측해 설정값을 확정한다.
- [ ] `CameraPublisher`가 카메라 하나의 FFmpeg를 `Popen(shell=False)`으로 실행하고
  안전하게 종료하도록 구현한다.
- [ ] `RtspFrameSource`가 RTSP blocking read를 전용 thread에서 수행하도록 구현한다.
- [ ] frame queue 없이 `(image, captured_at)` 최신값 하나만 교체한다.
- [ ] RTSP 단절 시 오래된 프레임을 지우고 같은 reader thread에서 재연결한다.
- [ ] 사용자·자세 카메라용 두 publisher와 두 reader를 FastAPI lifespan에 등록한다.
- [ ] 장치·RTSP·capture 설정과 `.env.example`을 갱신한다.
- [ ] 기본 비활성화 설정으로 카메라 없는 기존 실행과 테스트를 유지한다.
- [ ] 단위 테스트와 두 카메라 10분 동시 수신을 검증한다.

## 고정 경로

```text
user-cam:    rtsp://127.0.0.1:8554/user-cam
posture-cam: rtsp://127.0.0.1:8554/posture-cam
```

MediaMTX는 Task 05의 lifecycle resource가 아니다. FastAPI보다 먼저 호스트에서
실행되어 있고 위 두 path에 publish를 허용해야 한다.

## 제외 사항

- Docker, Compose와 MediaMTX 설치·실행·종료
- React WebRTC/HLS 미리보기
- 얼굴·자세·재실 detector와 frame 전처리
- `FrameSnapshot`, `CameraSnapshot`, `FrameSource` Protocol
- publisher manager, factory, registry와 별도 process supervisor
- 영상 녹화, frame API, MJPEG proxy와 database
- 인증, 방화벽과 외부 공개 네트워크 설계

## 금지 사항

- FFmpeg와 Python OpenCV가 같은 물리 `/dev/*` 카메라를 동시에 열지 않는다.
- `RtspFrameSource`는 물리 장치가 아니라 MediaMTX RTSP URL만 연다.
- 프레임을 queue에 누적하거나 과거 프레임을 저장하지 않는다.
- FastAPI가 기존 호스트 MediaMTX를 종료하거나 재설정하지 않는다.
- 두 카메라를 하나의 FFmpeg process나 하나의 reader 인스턴스로 합치지 않는다.

## 테스트

- [ ] 장치 없이 `Popen` 대역으로 publisher 시작·종료·중복 호출을 검증한다.
- [ ] 장치 없이 `VideoCapture` 대역으로 최신 프레임 교체·단절·재연결을 검증한다.
- [ ] 두 RTSP 경로를 동시에 10분 이상 읽고 최신 프레임이 계속 바뀌는지 확인한다.
- [ ] FFmpeg 하나를 종료해 다른 카메라 reader가 유지되는지 확인한다.
- [ ] FastAPI 종료 후 자신이 시작한 FFmpeg와 reader thread가 남지 않는지 확인한다.

## 완료 조건

두 카메라가 각자의 FFmpeg process를 통해 기존 MediaMTX에 발행되고, 각
`RtspFrameSource`가 자기 경로의 최신 프레임 하나를 제공한다. Task 06은 RTSP
연결을 다시 만들지 않고 이 두 source를 입력으로 사용할 수 있어야 한다.
