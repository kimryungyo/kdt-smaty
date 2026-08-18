# Vision 모델 배치

## 얼굴 모델 (OpenCV Zoo)

Provision the exact local filenames `face_detection_yunet_2023mar.onnx` and
`face_recognition_sface_2021dec.onnx`; binaries are intentionally ignored and
must not be committed. The `vision-runtime` image includes these files only, so
the main service image never carries Vision models. Its default model paths are
set inside that image and can be overridden through environment variables.

현재 운영 준비 시 내려받아 OpenCV로 load 검증한 파일의 SHA-256은 다음과 같다.

- `face_detection_yunet_2023mar.onnx`: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- `face_recognition_sface_2021dec.onnx`: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`

The sources are OpenCV Zoo's [YuNet face detection model](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
and [SFace recognition model](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface).
Operators must review the upstream model-card/license notices and comply with
their redistribution terms before deployment.

## 하단 YOLO pose 모델 배치

권장 파일명은 `yolo26n-pose.onnx`이며 저장소에는 모델 바이너리를 넣지 않는다. Vision 이미지를 만들기
전에 이 폴더에 provision하면 이미지 내부 `/app/assets/vision/models/`에 포함된다. 필요하면
`SMART_DESK_VISION__LOWER_POSE_MODEL_PATH`로 다른 경로를 지정할 수 있다. 빈 경로는 의도적으로
detector를 비활성화하고 `MODEL_UNAVAILABLE`를 유지한다.

이 구현을 검증한 로컬 파일의 SHA-256은 다음과 같다.

```text
bc0a1922ac18d3b30db47a6eab3ca93c4afb8a54e9f96689412d2e1b7a3c6594
```

현재 검증한 파일은 `zwh20081/yolo26-onnx`의 commit
`28dcc08aa5206533f1ebba0dce9e3f1490a8704e`에서 받은 ONNX export이다. 모델은 Ultralytics YOLO
계열이다. 배포·재배포 전에 제공자가 고지한 AGPL-3.0 또는 Enterprise 라이선스 조건과 조직의 사용
형태를 반드시 확인한다.
