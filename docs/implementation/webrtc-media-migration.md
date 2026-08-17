# WebRTC 영상 전환과 운영 준비도

## 결정

활성 영상 송출과 수신은 MediaMTX의 WebRTC 표준 endpoint로 통일한다.

```text
user/workspace V4L2 camera
  -> aiortc WHIP publisher
  -> MediaMTX :8889/{camera}/whip
  -> MediaMTX :8889/{camera}/whep
  -> aiortc WHEP frame source
  -> face/posture Vision

external bottom camera
  -> MediaMTX /bottom-cam
  -> http://127.0.0.1:8889/bottom-cam/whep
  -> posture Vision
```

- 송출 URL은 `http(s)://.../{path}/whip`만 허용한다.
- 수신 URL은 `http(s)://.../{path}/whep`만 허용한다.
- 사용자·작업대 카메라는 애플리케이션이 장치를 단독으로 열어 WHIP로 송출한다.
- 자세 카메라는 외부 publisher가 담당하며 이 애플리케이션은 `/bottom-cam/whep`만 읽는다.
- 대시보드 영상도 MediaMTX WebRTC reader page 또는 WHEP client를 사용한다.
- `~/sitting`의 WHEP 최신-frame 처리 방식과 자세 모델은 참고하지만 그 프로젝트의
  FastAPI/web server는 가져오지 않는다.

## 구현 선택

호스트 FFmpeg는 8.0.1이지만 Ubuntu 빌드에는 WHIP muxer가 빠져 있다. WHIP를 위해
FFmpeg와 libdatachannel을 별도 빌드하는 대신 Python 3.11에서 지원되는 `aiortc`와
PyAV를 애플리케이션 의존성으로 사용한다. 송출과 수신이 같은 SDP/ICE lifecycle을
공유하므로 단기 운영에서 실패 원인과 재연결 상태를 한 곳에서 관측할 수 있다.

기존 `CameraPublisher`와 `RtspFrameSource` 이름 및 RTSP 설정은 제거한다. Vision은
구체 transport가 아니라 `get_latest_frame()`, `is_connected()`, `get_last_error()` 계약만
사용한다.

## 전환 전 부족한 부분

| 영역 | 현재 상태 | 완료 조건 |
| --- | --- | --- |
| user/workspace 송출 | FFmpeg RTSP publisher | WHIP session 생성, 종료 DELETE, 재시작 검증 |
| user/workspace/posture 수신 | OpenCV RTSP reader | WHEP session, 최신 BGR frame, bounded backoff |
| 설정 | `rtsp://`만 허용 | WHIP/WHEP endpoint별 검증과 WebRTC 기본값 |
| Vision 결합 | `RtspFrameSource` 구체 타입 | transport-neutral frame source 계약 |
| 대시보드 | 기존 MediaMTX URL 사용 여부 재검증 필요 | WebRTC URL만 노출 |
| 운영 환경 | `.env`가 8554 RTSP를 가리킴 | 8889 WHIP/WHEP로 전환 |
| 실카메라 | RTSP publisher 두 개 실행 중 | RTSP process 0개, 두 WHIP path 프레임 확인 |
| 자세 입력 | `/bottom-cam` publisher 부재가 관측됨 | 외부 publisher 재기동 후 WHEP frame 확인 |
| 얼굴 등록 | 코드와 UI는 구현됨, 현재 user 영상에 얼굴 없음 | 밝은 단일 얼굴로 등록/인식 검증 |
| 음성 | 마이크 미연결을 readiness에서 격리함 | 마이크 연결 후 재기동하여 장치/대화 검증 |

## 운영 안전 조건

- WHIP publisher가 실패해도 서버 전체 readiness와 MQTT desk 제어는 유지한다.
- WHEP는 첫 frame을 받기 전 `connected`로 보고하지 않는다.
- 연결 끊김 즉시 stale frame을 지우고 bounded exponential backoff로 재연결한다.
- stop은 peer connection을 닫고 MediaMTX가 준 session resource를 DELETE한다.
- 영상 오류 로그에는 endpoint나 인증 정보 전체를 남기지 않고 camera 이름과 상태만 남긴다.
- 자동 책상 이동 기본값은 계속 비활성화한다.

## 검증 순서

1. endpoint 정규화, SDP 교환, frame 최신성, 종료와 backoff를 단위 테스트한다.
2. 전체 Python 테스트와 frontend build를 실행한다.
3. 운영 `.env`를 WHIP/WHEP로 바꾸고 systemd 서비스를 재기동한다.
4. MediaMTX WebRTC session과 user/workspace WHEP frame을 확인한다.
5. 실행 중인 FFmpeg RTSP publisher와 8554 수신 연결이 없는지 확인한다.
6. `/bottom-cam` 외부 publisher가 살아나면 자세 추론을 확인한다.
7. 밝은 환경에서 얼굴 등록, 인식, 세션 시작/중단을 순서대로 검증한다.

## 2026-08-17 운영 검증 결과

- `user-cam`과 `workspace-cam` WHIP publish가 MediaMTX 1.19.2에 연결됐다.
- WHEP로 user `1920x1080`, workspace `1920x1080` BGR frame을 실제 수신했다.
- user Vision camera는 `ONLINE`이고 애플리케이션 `/health/ready`는 `200 ready`다.
- 운영 MediaMTX의 RTSP listener를 비활성화했으며 호스트 `:8554` listener와 FFmpeg
  publisher process가 없음을 확인했다.
- workspace는 publish-only인 현재 역할에 맞춰 운영 capture를 `1920x1080@15`로 낮춰
  5MP 소프트웨어 인코딩 부하를 줄였다.
- `/bottom-cam/whep`은 외부 publisher가 현재 보이지 않아 `404` bounded backoff 상태다.
  로컬 `~/sitting`은 이 경로의 WHEP consumer일 뿐 publisher가 아니며, 외부 하체 카메라
  송출 주체를 WHIP로 재기동해야 자세·session 라이브 검증을 계속할 수 있다.
- 마이크는 미연결 상태라 Voice만 `input_device_name_invalid`이고 전역 readiness와 영상은
  유지된다.
