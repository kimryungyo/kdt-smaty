# 05. MediaMTX 영상 인프라

## 목표

두 물리 웹캠을 Python 밖의 FFmpeg publisher가 단독으로 열고 MediaMTX에
발행한다. Python 업로더 없이 Vision과 React가 같은 스트림을 사용하게 한다.

## 확정 데이터 흐름

```text
USB webcam → FFmpeg → RTSP → MediaMTX
                              ├─ RTSP → Python Vision
                              └─ WebRTC/HLS → React
```

## 작업 목록

- [ ] `infra/compose.yaml`, `infra/mediamtx.yml`과 운영 README를 작성한다.
- [ ] 사용자·자세 카메라의 안정적인 `/dev` 별칭을 확정한다.
- [ ] 카메라마다 독립 FFmpeg publisher 서비스를 구성한다.
- [ ] RTSP 경로를 `user-cam`, `posture-cam`으로 고정한다.
- [ ] 해상도, FPS, 입력 포맷과 H.264 인코딩 preset을 실측해 결정한다.
- [ ] publisher 또는 MediaMTX 재시작 시 자동 복구를 확인한다.
- [ ] React 미리보기는 WebRTC와 HLS의 지연·호환성을 비교해 하나를 선택한다.
- [ ] 현재 `VisionSettings`와 `.env.example`의 `/dev/*` 입력을 RTSP URL 설정으로
  변경한다.
- [ ] 방화벽과 외부 노출 포트를 운영 문서에 기록한다.

## 금지 사항

- Python `MediaMtxUploader` 또는 프레임 업로드 API를 만들지 않는다.
- FFmpeg와 Python OpenCV가 같은 `/dev/video*` 장치를 동시에 열지 않는다.
- MediaMTX·FFmpeg 프로세스를 `AppContainer` singleton으로 보관하지 않는다.

## 테스트

- [ ] 두 RTSP 경로를 동시에 10분 이상 읽고 단절·프레임 지연을 기록한다.
- [ ] 브라우저 두 스트림의 지연과 CPU·메모리 사용량을 측정한다.
- [ ] FFmpeg 하나를 강제 재시작해 다른 카메라와 FastAPI가 유지되는지 확인한다.

## 완료 조건

두 카메라가 동시에 안정적으로 발행되고, RTSP reader와 브라우저에서 같은
스트림을 확인한다. Python 저장소에는 업로더 구현이 없어야 한다.
