"""
中文文本转语音 Web 应用
基于 Flask + Edge-TTS
"""

import asyncio
import os
import uuid
import time
import subprocess
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts
from imageio_ffmpeg import get_ffmpeg_exe

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB

FFMPEG = get_ffmpeg_exe()

# 音频文件临时存放目录
AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

# 中文语音配置
VOICES = {
    "male": "zh-CN-YunjianNeural",       # 男声
    "female": "zh-CN-XiaoxiaoNeural",    # 女声
    "boy": "zh-CN-YunxiNeural",          # 男生-少年
    "girl": "zh-CN-XiaoyiNeural",        # 女生-少年
}


async def generate_speech(text: str, voice_type: str) -> str:
    """生成语音文件，返回文件路径（带重试）"""
    if voice_type not in VOICES:
        raise ValueError(f"不支持的音色: {voice_type}")
    
    filename = f"{uuid.uuid4().hex}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)
    
    last_err = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, VOICES[voice_type])
            await communicate.save(filepath)
            return filename
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))  # 递增延迟: 1.5s, 3s
            else:
                break
    
    raise RuntimeError(f"TTS生成失败(重试3次): {last_err}") from last_err


async def _generate_one_segment(seg: dict, idx: int) -> str:
    """生成单段音频，供并行调用"""
    text = seg.get("text", "").strip()
    voice = seg.get("voice", "female")
    if not text:
        return None
    if voice not in VOICES:
        voice = "female"  # 非法音色兜底
    filepath = await generate_speech(text, voice)
    return os.path.join(AUDIO_DIR, filepath)


async def generate_dialogue(segments: list) -> str:
    """
    生成对话语音，多段合并成一个文件（并行生成各段）
    segments: [{"text": "...", "voice": "male"}, ...]
    """
    # 并行生成每段音频
    tasks = [_generate_one_segment(seg, idx) for idx, seg in enumerate(segments)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    files = []
    errors = []
    for r in results:
        if isinstance(r, Exception):
            errors.append(r)
        elif r:
            files.append(r)
    
    # 如果有任何一段失败，清理已生成的文件
    if errors:
        for f in files:
            try: os.remove(f)
            except: pass
        raise RuntimeError(f"对话生成失败: {errors[0]}")
    
    if not files:
        raise ValueError("没有有效的文本内容")
    
    # 给每段音频尾部添加 0.4 秒静音（段间停顿）
    padded_files = []
    for i, f in enumerate(files):
        padded = os.path.join(AUDIO_DIR, f"pad_{i}_{uuid.uuid4().hex}.mp3")
        cmd = [
            FFMPEG, "-y", "-i", f,
            "-af", "apad=pad_dur=0.4",
            "-acodec", "libmp3lame", "-q:a", "2",
            padded
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg静音处理失败: {result.stderr}")
        padded_files.append(padded)
    
    # 用 concat 滤镜合并所有音频
    inputs = []
    for f in padded_files:
        inputs.extend(["-i", f])
    
    filter_parts = []
    for i in range(len(padded_files)):
        filter_parts.append(f"[{i}:a]")
    filter_str = "".join(filter_parts) + f"concat=n={len(padded_files)}:v=0:a=1[outa]"
    
    merged_filename = f"dialogue_{uuid.uuid4().hex}.mp3"
    merged_path = os.path.join(AUDIO_DIR, merged_filename)
    
    cmd = [FFMPEG, "-y"] + inputs + [
        "-filter_complex", filter_str,
        "-map", "[outa]",
        "-acodec", "libmp3lame", "-q:a", "2",
        merged_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg合并失败: {result.stderr}")
    
    # 清理临时文件
    for f in files + padded_files:
        try:
            os.remove(f)
        except:
            pass
    
    return merged_filename


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/tts", methods=["POST"])
def tts():
    """文本转语音 API"""
    data = request.get_json()
    text = data.get("text", "").strip()
    voice_type = data.get("voice", "female")
    
    if not text:
        return jsonify({"success": False, "error": "请输入文本内容"}), 400
    
    if len(text) > 3000:
        return jsonify({"success": False, "error": "文本过长，请控制在 3000 字以内"}), 400
    
    try:
        filename = asyncio.run(generate_speech(text, voice_type))
        return jsonify({
            "success": True,
            "filename": filename,
            "voice": "男声" if voice_type == "male" else "女声"
        })
    except Exception as e:
        import traceback
        print("=" * 60)
        print(f"[ERROR] tts_dialogue 异常: {e}")
        print(traceback.format_exc())
        print("=" * 60)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/audio/<filename>")
def serve_audio(filename):
    """提供音频文件下载/播放"""
    filepath = os.path.join(AUDIO_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, mimetype="audio/mpeg")
    return "文件不存在", 404


@app.route("/tts_dialogue", methods=["POST"])
def tts_dialogue():
    """对话模式：多段文本合并成一个音频"""
    data = request.get_json()
    segments = data.get("segments", [])
    
    if not segments or not isinstance(segments, list):
        return jsonify({"success": False, "error": "请提供对话分段数据"}), 400
    
    # 检查总字数
    total_chars = sum(len(seg.get("text", "")) for seg in segments)
    if total_chars > 5000:
        return jsonify({"success": False, "error": "文本过长，总字数请控制在 5000 字以内"}), 400
    
    try:
        filename = asyncio.run(generate_dialogue(segments))
        return jsonify({
            "success": True,
            "filename": filename,
            "type": "对话合并"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def merge_audio_files(filepaths: list, pause_sec: float = 0) -> str:
    """合并多个音频文件成一个"""
    files = filepaths.copy()
    
    # 如果设置了停顿，给每段尾部加静音
    if pause_sec > 0:
        padded = []
        for i, f in enumerate(files):
            p = os.path.join(AUDIO_DIR, f"upad_{i}_{uuid.uuid4().hex}.mp3")
            cmd = [
                FFMPEG, "-y", "-i", f,
                "-af", f"apad=pad_dur={pause_sec}",
                "-acodec", "libmp3lame", "-q:a", "2",
                p
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            padded.append(p)
        files = padded
    
    # 用 concat 滤镜合并
    inputs = []
    for f in files:
        inputs.extend(["-i", f])
    
    filter_parts = []
    for i in range(len(files)):
        filter_parts.append(f"[{i}:a]")
    filter_str = "".join(filter_parts) + f"concat=n={len(files)}:v=0:a=1[outa]"
    
    merged_filename = f"merged_{uuid.uuid4().hex}.mp3"
    merged_path = os.path.join(AUDIO_DIR, merged_filename)
    
    cmd = [FFMPEG, "-y"] + inputs + [
        "-filter_complex", filter_str,
        "-map", "[outa]",
        "-acodec", "libmp3lame", "-q:a", "2",
        merged_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # 清理临时文件
    for f in files:
        try:
            os.remove(f)
        except:
            pass
    
    return merged_filename


@app.route("/merge", methods=["POST"])
def merge():
    """音频拼接：合并多个上传的 MP3 文件"""
    files = request.files.getlist("files")
    pause = float(request.form.get("pause", "0"))
    
    if not files or len(files) < 2:
        return jsonify({"success": False, "error": "请至少上传2个音频文件"}), 400
    
    if len(files) > 50:
        return jsonify({"success": False, "error": "一次最多拼接50个文件"}), 400
    
    # 保存上传文件
    saved = []
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ['.mp3', '.mpeg']:
            # 清理已保存的文件
            for s in saved:
                try: os.remove(s)
                except: pass
            return jsonify({"success": False, "error": f"不支持的文件格式: {f.filename}，仅支持 MP3"}), 400
        
        filename = f"{uuid.uuid4().hex}_{f.filename}"
        filepath = os.path.join(AUDIO_DIR, filename)
        f.save(filepath)
        saved.append(filepath)
    
    try:
        merged_filename = merge_audio_files(saved, pause)
        return jsonify({
            "success": True,
            "filename": merged_filename,
            "type": "音频拼接",
            "count": len(files)
        })
    except Exception as e:
        # 清理临时文件
        for s in saved:
            try: os.remove(s)
            except: pass
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/cleanup", methods=["POST"])
def cleanup():
    """清理过期的音频文件"""
    try:
        now = time.time()
        count = 0
        for f in os.listdir(AUDIO_DIR):
            filepath = os.path.join(AUDIO_DIR, f)
            if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > 3600:
                os.remove(filepath)
                count += 1
        return jsonify({"success": True, "removed": count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 50)
    print("中文TTS Web服务已启动")
    print("请在浏览器中访问: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)
