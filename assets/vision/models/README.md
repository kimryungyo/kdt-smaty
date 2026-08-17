# Vision 모델 배치

## 얼굴 모델 (OpenCV Zoo)

Provision the exact local filenames `face_detection_yunet_2023mar.onnx` and
`face_recognition_sface_2021dec.onnx`; binaries are intentionally ignored and
must not be committed. Set their local paths through `SMART_DESK_FACE__DETECTOR_MODEL_PATH`
and `SMART_DESK_FACE__EMBEDDING_MODEL_PATH`.

현재 운영 준비 시 내려받아 OpenCV로 load 검증한 파일의 SHA-256은 다음과 같다.

- `face_detection_yunet_2023mar.onnx`: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- `face_recognition_sface_2021dec.onnx`: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`

The sources are OpenCV Zoo's [YuNet face detection model](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
and [SFace recognition model](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface).
Operators must review the upstream model-card/license notices and comply with
their redistribution terms before deployment.

## 하단 YOLO pose 모델 배치

권장 파일명은 `yolo26n-pose.onnx`이며 저장소에는 모델 바이너리를 넣지 않는다. 운영자는 모델을
별도로 provision한 뒤 `SMART_DESK_VISION__LOWER_POSE_MODEL_PATH`에 로컬 경로를 설정한다. 빈 경로는
의도적으로 detector를 비활성화하고 `MODEL_UNAVAILABLE`를 유지한다.

이 구현을 검증한 로컬 파일의 SHA-256은 다음과 같다.

```text
93fc5e1d6b7690f33b4e1d60d6e9aec1cea14bdbc361bfae11778969be662078
```

모델은 Ultralytics YOLO 계열이다. 배포·재배포 전에 Ultralytics AGPL-3.0 또는 Enterprise 라이선스
조건과 조직의 사용 형태를 반드시 확인한다.
