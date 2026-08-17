# 04. Vision 관측

## 사용자 결과

서버는 Dashboard가 열려 있지 않아도 두 카메라의 최신 프레임을 계속 처리해 책상 영역의
인원수, 재실과 앉음·섬 상태를 제공한다. 카메라나 모델이 불확실하면 오래된 결과 대신
명확한 `UNKNOWN`과 차단 근거를 반환한다.

이 작업은 얼굴로 profile을 식별하지 않는다. 다만 상단 재실 count에 필요한 얼굴 검출 결과는
한 번만 만들고 공유한다. [얼굴 식별과 사용자 세션](05-face-identity-session.md)은 이 fresh
얼굴 box를 받아 정렬·품질 검사·embedding·profile 비교를 수행한다.

## 현재 상태

- 사용자·자세 카메라용 `CameraPublisher`와 `RtspFrameSource`가 lifecycle에 등록된다.
- 각 source는 queue 없이 최신 `(frame, captured_at)` 하나를 유지하고 재연결한다.
- `VisionService`가 container의 user(상단)·posture(하단) source를 소유하고 최신 frame만
  소비한다. `workspace`는 Vision 자세 입력으로 대체하지 않는다.
- `/api/vision/status`는 fail-closed raw/stable snapshot과 camera freshness를 제공한다.
- 기본 `NoopVisionDetector`는 실제 관측인 척하지 않고 `MODEL_UNAVAILABLE`를 반환한다.
- 선택 하단 detector는 OpenCV DNN YOLO pose ONNX의 `(1,300,57)` end-to-end NMS 출력을 검증한다.
  최신 posture RTSP frame 전체를 letterbox 640으로 처리하고 2Hz에서 최신 frame 하나만 사용한다.
  상단 model이 unavailable이어도 fresh singleton 하단의 frame-level raw posture는 보이지만,
  stable posture와 association/AUTO는 기존 양쪽 camera singleton 결합 조건을 계속 요구한다.
- ROI calibration 전에는 전체 하단 frame을 사용한다. 주변 통행도 count되면 보수적으로
  `MULTIPLE_PEOPLE`가 되어 자동화를 차단한다. WHEP/aiortc, FastAPI/web UI, MJPEG/JPEG polling이나
  preview endpoint는 구현하거나 복사하지 않았다.
- Dashboard는 Vision snapshot을 표시한다. browser camera preview와 더 넓은 debug 근거 표시는
  아직 미완료다.

## 첫 구현의 관측 범위

첫 버전은 범용 다중 사람 추적이나 카메라 간 Re-ID를 목표로 하지 않는다. 상단 책상 ROI의
몸체 또는 얼굴 한 명과 하단 책상 ROI의 하체 한 명이 fresh하게 결합된 경우를 정상 자동화
후보로 보고 다음 상황은 fail-closed한다. 상단 얼굴과 몸체는 같은 사람의 존재 근거로
결합하며 count를 더하지 않는다.

- 어느 카메라든 책상 ROI에서 여러 사람이 검출됨
- 두 카메라의 인원수 또는 관측 시각이 허용 범위를 벗어남
- 현재 자세를 어느 한 사람에게 귀속할 수 없음
- frame, detector 결과 또는 model 상태가 만료됨

실제 설치에서 두 카메라의 시야가 충분히 겹치지 않는다면 “동일 인물”을 추정하지 않고
자동화 차단 조건과 필요한 추가 추적 작업을 문서화한다.

## 공개 관측 모델

task 01에서 확정한 이름으로 Vision API는 최소한 다음을 분리해 제공한다.

| 정보 | 예시 내용 |
| --- | --- |
| 카메라 | 연결 상태, 마지막 frame 시각, frame age와 오류 |
| 인원수 | 카메라별 raw count, 안정화 count와 ROI |
| 재실 | `PRESENT_SINGLE`, `VACANT`, `MULTIPLE`, `UNKNOWN`, 관측·만료 시각 |
| 자세 | `SITTING`, `STANDING`, `UNKNOWN`, 후보와 유지 시간 |
| 결합 상태 | 두 카메라 시각 일치, 인원수 일치와 자동화 사용 가능 여부 |

내부 freshness 판단에는 monotonic clock을 사용하고, API에는 브라우저가 해석할 수 있는 wall
clock 시각 또는 age를 일관되게 제공한다. 프로세스 재시작 전의 monotonic 값을 저장하거나
API로 노출하지 않는다.

## 구현 단계

실물 하단 카메라가 없어도 snapshot 모델, detector adapter, fake frame 기반 안정화·freshness,
lifecycle과 API를 먼저 구현한다. 실제 ROI 좌표, 모델 선택의 최종 확인과 threshold 보정은
카메라 연결 뒤 완료하며, 이 실물 항목 때문에 task 전체 착수를 막지 않는다.

### 입력과 전처리

- [x] 두 `RtspFrameSource`를 container에서 Vision service에 주입할 수 있게 보관한다.
- [ ] 설정 기반 카메라 경로·방향·해상도·FPS와 책상 ROI 구조를 만들고 실물 연결 뒤 값을 확인한다.
- [x] 하단 detector 2Hz 처리 상한, frame/result freshness와 detector threshold 설정을 정의한다.
- [ ] 상단 몸체/얼굴과 하단 하체의 책상 ROI 및 singleton 결합을 구현한다.
- [ ] 상단 얼굴 detector를 한 번 load·실행하고 fresh box/count snapshot을 task 05가 재사용할
  수 있게 한다. task 05가 별도 얼굴 detector loop를 만들지 않게 한다.
- [x] 같은 frame을 중복 추론하지 않도록 captured time을 추적한다.
- [x] 하단 full-frame letterbox 640, RGB blob 전처리와 COCO hip/knee/ankle 기하 판정을 구현한다.

### detector와 안정화

- [x] 하단 사람 수·자세에 OpenCV DNN YOLO pose ONNX를 적용한다. 모든 threshold 이상 row를
  count하고 정확히 한 명일 때만 stateless raw 자세를 계산한다. 상단 detector는 계속 미구현이다.
- [x] detector 추론을 event loop 밖에서 수행한다.
- [x] 같은 detector/model의 upper/lower 호출은 작은 lock으로 직렬화한다.
- [x] raw 결과와 안정화 결과를 구분하고 앉음·섬 후보 유지 timer를 구현한다.
- [x] frame·결과 만료, model 오류와 task 종료에서 `UNKNOWN`으로 전환한다.
- [x] 두 카메라 결과의 시각·인원수 일치 여부를 계산한다.
- [x] 단일 재실과 자세의 안정화는 monotonic clock과 이전 결합 이후 양쪽 모두 새 frame인
  distinct pair에서만 전진한다.

### lifecycle과 API

- [x] Vision loop를 container singleton과 lifecycle resource로 등록한다.
- [x] 시작 전에는 안전한 초기 snapshot, 종료 시에는 만료된 snapshot을 제공한다.
- [x] `/api/vision/status`에 raw·안정화 상태와 차단 이유를 camelCase로 노출한다.
- [x] Task 04의 `identity`는 `UNKNOWN` placeholder이며 raw frame·face box·vector는 API에
  노출하지 않는다. Task 05는 `get_fresh_face_observation()`의 내부 frame+box만 소비한다.
- [x] 일반 Dashboard에는 최소 Vision snapshot을 연결했다. preview와 상세 debug 근거는 별도
  미완료 항목으로 유지한다.
- [ ] MediaMTX WebRTC/HLS preview를 실제 브라우저에서 실측해 한 방식을 확정한다.

## 제외 범위

- 얼굴 정렬·품질 검사·임베딩, 등록 profile 비교와 현재 사용자 session
- 카메라 간 범용 사람 Re-ID 또는 장기 trajectory 저장
- 영상 녹화, raw frame DB 저장과 FastAPI JPEG polling API
- 자세에 따른 실제 책상 이동

## 검증

- fake 최신 frame으로 재실·자세 후보와 안정화 전이를 결정적으로 재현한다.
- 같은 frame을 여러 loop에서 새 관측으로 반복 사용하지 않는다.
- 오래된 frame, RTSP 단절과 model 예외가 새 관측이나 PRESENT로 남지 않는다.
- 다중 사용자와 두 카메라 count·timestamp 불일치가 결합 불가 상태가 된다.
- 처리 속도가 입력 FPS보다 느려도 frame queue와 메모리가 지속 증가하지 않는다.
- 추론 부하 중 health, Dashboard 조회와 STOP 응답 시간이 허용 기준을 넘지 않는다.
- 두 카메라를 동시에 장시간 실행해 독립 재연결과 최신 frame 교체를 확인한다.

## 실물 확인 항목

- 하단 ONNX adapter의 fake output과 제공 sample 자동 회귀는 완료했지만, 실제 RTSP camera, CPU 지연,
  threshold와 ROI 보정은 수행하지 않았다. 상단 detector/얼굴 embedding/전체 association도 미완료다.
- MediaMTX WebRTC/HLS preview 방식과 상단·하단 camera 배치, FPS·조명·책상 주변 통행에 대한
  실측은 아직 수행하지 않았다.
- 앉음·섬·이탈과 책상 주변 통행을 ROI에서 촬영해 오검출 패턴을 기록한다.
- 조명, 안경·모자, 의자 위치와 책상 높이 변화가 사람 수·자세 판정에 미치는 영향을 본다.
- camera 한 대 제거, MediaMTX 중단과 복귀 후 snapshot과 memory 사용량을 확인한다.

## 완료 조건

- 재실·자세·인원수와 각 관측 freshness를 API로 독립 조회할 수 있다.
- 불확실·다중·불일치·오래된 결과가 자동화 가능 상태로 표현되지 않는다.
- Dashboard·STOP event loop와 frame memory가 추론 지연의 영향을 받지 않는다.
- 얼굴 task가 source나 얼굴 detector loop를 다시 만들지 않고 fresh 검출 결과를 입력으로 사용할 수 있다.
