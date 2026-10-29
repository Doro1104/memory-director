# 第三方来源

MIT 只覆盖本项目自身代码，不覆盖第三方依赖、模型或配乐。源码包没有打包 Python、依赖二进制、FFmpeg 二进制或模型权重；安装时按固定版本和公开来源下载，保留其许可证。

| 用途 | 来源与许可 |
| --- | --- |
| 人脸检测 | OpenCV Zoo YuNet；MIT，下载的 face_detection_yunet-LICENSE.txt |
| 人脸特征 | OpenCV Zoo SFace；Apache-2.0，下载的 face_recognition_sface-LICENSE.txt |
| 画面语义 | OpenAI CLIP / Xenova ONNX 导出；MIT，models/clip/LICENSE.txt |
| 中文语义 | sentence-transformers/clip-ViT-B-32-multilingual-v1；Apache-2.0，models/multilingual/LICENSE.txt |
| 人物表情 | Google MediaPipe Face Landmarker；按 Google 模型发布页和 MediaPipe 所附条款使用，来源见 models-manifest.json |
| 可选配乐 | music-library.json 中三首配乐由作者发布为 CC0-1.0；SHA-256 校验固定文件，目录记录原作者和来源页 |
| 服务/媒体依赖 | requirements.txt 固定版本；Flask、Werkzeug、Pillow、pillow-heif、OpenCV、NumPy、ONNX Runtime、tokenizers、MediaPipe、imageio-ffmpeg、Cheroot、cryptography 等各有许可证，由安装包附带 |

FFmpeg 的具体许可取决于下载的构建选项；本项目使用 imageio-ffmpeg 下载的 FFmpeg，相关二进制包含 libx264 等组件。若另行分发带二进制的安装包，必须额外核对该构建的许可和对应源码义务，不能仅附本项目 MIT。本预览包采用安装时下载的方式。

官方来源：[OpenCV Zoo](https://github.com/opencv/opencv_zoo)、[CLIP](https://github.com/openai/CLIP)、[多语言文本模型](https://huggingface.co/sentence-transformers/clip-ViT-B-32-multilingual-v1)、[MediaPipe](https://github.com/google-ai-edge/mediapipe)、[FFmpeg](https://ffmpeg.org/legal.html)。安装脚本与模型清单中记录了具体下载 URL 和固定校验值。
