# Voice third-party software와 model

AI 스피커 기능은 현재 비상업 개인 프로젝트 범위에서 아래 package와 내장 model을
사용한다. 상업적 사용이나 배포 범위가 생기면 model license 적합성을 다시 검토하고,
현재 `hey_jarvis`를 그대로 사용하지 않는다.

## openWakeWord

- 설치 version: `openwakeword==0.6.0`
- PyPI: <https://pypi.org/project/openwakeword/0.6.0/>
- upstream: <https://github.com/dscripka/openWakeWord>
- package code license: Apache License 2.0
- 추론 framework: ONNX Runtime
- 확인 환경: x86_64, Linux, Python 3.11.15

package 최초 사용 시 공식 GitHub release에서 feature model과 Wake Word model
파일을 package cache에 다운로드하고 이후 재사용한다. 추론에는 ONNX 파일만
사용하며 repository에 model binary를 복제하지 않는다.

## hey_jarvis model

- model: `hey_jarvis` official ONNX model
- 원본 정보: <https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md>
- model license: Creative Commons Attribution-NonCommercial-ShareAlike 4.0
  International (CC BY-NC-SA 4.0)
- license 전문: <https://creativecommons.org/licenses/by-nc-sa/4.0/>

이 프로젝트는 `hey_jarvis` model과 그 변형물을 비상업 범위에서 사용하고, 저작자 표시와
동일조건변경허락 의무를 유지한다. package의 Apache-2.0 code license가 model의 별도
CC BY-NC-SA 4.0 조건을 대체하지 않는다.
