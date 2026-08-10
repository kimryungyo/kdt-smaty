# Voice third-party software와 model

AI 스피커 기능은 현재 비상업 개인 프로젝트 범위에서 아래 package와 내장 model을
사용한다. 상업적 사용이나 배포 범위가 생기면 model license 적합성을 다시 검토하고,
현재 `hey_jarvis`를 그대로 사용하지 않는다.

## pyopen-wakeword

- 설치 version: `pyopen-wakeword==1.1.0`
- PyPI: <https://pypi.org/project/pyopen-wakeword/1.1.0/>
- upstream: <https://github.com/rhasspy/pyopen-wakeword>
- package code license: Apache License 2.0
- 확인 wheel: `pyopen_wakeword-1.1.0-py3-none-manylinux_2_35_x86_64.whl`
- wheel SHA-256: `754347a59de2b3d378a0cbc404a8b41164036e74fa1c185f88056974e4bfb6b4`
- 확인 환경: x86_64, glibc 2.43, Python 3.14.4

package wheel에 TensorFlow Lite C runtime, feature model과 Wake Word model이 포함된다.
애플리케이션 시작 시 model을 다운로드하지 않으며 repository에도 binary를 복제하지
않는다.

## hey_jarvis model

- model: `Model.HEY_JARVIS` builtin TFLite
- 원본 정보: <https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md>
- model license: Creative Commons Attribution-NonCommercial-ShareAlike 4.0
  International (CC BY-NC-SA 4.0)
- license 전문: <https://creativecommons.org/licenses/by-nc-sa/4.0/>

이 프로젝트는 `hey_jarvis` model과 그 변형물을 비상업 범위에서 사용하고, 저작자 표시와
동일조건변경허락 의무를 유지한다. package의 Apache-2.0 code license가 model의 별도
CC BY-NC-SA 4.0 조건을 대체하지 않는다.
