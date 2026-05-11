# 中文文本转语音（TTS）

基于 Flask + 微软 Edge-TTS 的本地 Web 应用，支持单段语音合成、多角色对话合成、音频拼接合并等功能。

## 功能

- **单段模式**：输入文本，选择音色（男声/女声/少年音），一键生成语音
- **对话模式**：按 `姓名：内容` 格式输入多行文本，自动为不同角色分配不同音色并合并
- **音频拼接**：上传多个 MP3 文件，按顺序合并，支持设置段间停顿

## 环境要求

- Python 3.8+
- 网络连接（Edge-TTS 调用微软在线语音合成接口）

## 安装

```bash
pip install -r requirements.txt
```

## 启动

```bash
python app.py
```

然后在浏览器中打开 `http://127.0.0.1:5000` 即可使用。

## 命令行工具

项目也提供了独立的命令行 TTS 工具：

```bash
# 生成女声
python tts.py "你好世界" -v female -o hello.mp3

# 生成男声
python tts.py "今天天气不错" -v male -o weather.mp3

# 生成两个音色的试听文件
python tts.py --preview
```
