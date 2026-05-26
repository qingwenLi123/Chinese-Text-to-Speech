"""
中文文本转语音 Web 应用
基于 Flask + Edge-TTS
"""

import asyncio
import os
import uuid
import time
import subprocess
import re
import random
from flask import Flask, render_template, request, jsonify, send_file
import edge_tts
from imageio_ffmpeg import get_ffmpeg_exe

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB

FFMPEG = get_ffmpeg_exe()

# 音频文件临时存放目录
AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

# 中文语音配置（含方言）
VOICES = {
    # 普通话
    "male": "zh-CN-YunjianNeural",       # 男声
    "female": "zh-CN-XiaoxiaoNeural",    # 女声
    "boy": "zh-CN-YunxiNeural",          # 男生-少年
    "girl": "zh-CN-XiaoyiNeural",        # 女生-少年
    # 方言
    "cantonese_male": "zh-HK-WanLungNeural",       # 粤语男声
    "cantonese_female": "zh-HK-HiuGaaiNeural",     # 粤语女声
    "dongbei": "zh-CN-liaoning-XiaobeiNeural",     # 东北话
    "shaanxi": "zh-CN-shaanxi-XiaoniNeural",       # 陕西话
    "taiwan_male": "zh-TW-YunJheNeural",           # 台湾腔男声
    "taiwan_female": "zh-TW-HsiaoYuNeural",        # 台湾腔女声
}

# 口语化替换规则（概率触发）
SPOKEN_REPLACEMENTS = {
    "好的": ["好嘞", "行", "成啊", "好的好的"],
    "什么": ["啥", "什么呀"],
    "这个": ["这个嘛", "这个...这个"],
    "那个": ["那个...那个", "那个嘛"],
    "不是": ["不是不是", "不是啊"],
    "就是": ["就是就是"],
    "知道": ["知道知道"],
    "明白": ["明白明白"],
    "可以": ["可以可以", "没问题没问题"],
    "谢谢": ["谢了", "谢谢啊"],
}

FILLERS = ["嗯...", "啊...", "那个...", "呃...", "这个..."]


def humanize_text(text: str) -> str:
    """
    将标准文本转换为带 SSML 的真人化口语文本
    功能：口语化替换 + 填充词 + 随机重复 + SSML 停顿
    （注：Edge-TTS 对 <prosody rate/pitch> 支持不稳定，只使用 <break>）
    """
    result = text
    
    # 阶段 1：口语化文本替换（每条规则最多触发一次，30% 概率）
    for key, choices in SPOKEN_REPLACEMENTS.items():
        if key in result and random.random() < 0.3:
            result = result.replace(key, random.choice(choices), 1)
    
    # 阶段 2：句首填充词（20% 概率）
    if random.random() < 0.2:
        result = random.choice(FILLERS) + result
    
    # 阶段 3：随机重复单字（模拟口吃/强调，10% 概率）
    result = re.sub(
        r'([我你他她它])',
        lambda m: m.group(1) * 2 if random.random() < 0.1 else m.group(1),
        result
    )
    
    # 阶段 4：纯文本停顿模拟（Edge-TTS 不支持 <break> SSML 标签）
    # 逗号后 40% 概率加省略号，模拟迟疑/换气停顿
    result = re.sub(
        r'，',
        lambda m: '，……' if random.random() < 0.4 else '，',
        result
    )
    # 句号/问号/叹号后 50% 概率加省略号，模拟思考停顿
    result = re.sub(
        r'([。！？])',
        lambda m: m.group(1) + '……' if random.random() < 0.5 else m.group(1),
        result
    )
    
    return result


async def generate_speech(text: str, voice_type: str, real_mode: bool = False) -> str:
    """生成语音文件，返回文件路径（带重试）。real_mode=True 时启用真人模式"""
    if voice_type not in VOICES:
        raise ValueError(f"不支持的音色: {voice_type}")
    
    # 真人模式：文本真人化 + SSML
    if real_mode:
        text = humanize_text(text)
    
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


async def _generate_one_segment(seg: dict, idx: int, real_mode: bool = False) -> str:
    """生成单段音频，供并行调用。real_mode=True 时启用真人模式"""
    text = seg.get("text", "").strip()
    voice = seg.get("voice", "female")
    if not text:
        return None
    if voice not in VOICES:
        voice = "female"  # 非法音色兜底
    filepath = await generate_speech(text, voice, real_mode=real_mode)
    return os.path.join(AUDIO_DIR, filepath)


async def generate_dialogue(segments: list, noise_type: str = "none", noise_volume: float = 0.3, real_mode: bool = False) -> str:
    """
    生成对话语音，多段合并成一个文件（并行生成各段），可选叠加环境噪音、真人模式
    segments: [{"text": "...", "voice": "male"}, ...]
    """
    # 并行生成每段音频，传入 real_mode
    tasks = [_generate_one_segment(seg, idx, real_mode=real_mode) for idx, seg in enumerate(segments)]
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
    
    # 如果启用了环境噪音，叠加噪音层
    if noise_type != "none":
        valid_noises = {"white": "white", "pink": "pink", "brown": "brown"}
        if noise_type not in valid_noises:
            # 清理临时文件
            for f in files + padded_files:
                try: os.remove(f)
                except: pass
            try: os.remove(merged_path)
            except: pass
            raise ValueError(f"不支持的噪音类型: {noise_type}")
        
        mixed_filename = f"dialogue_{uuid.uuid4().hex}.mp3"
        mixed_path = os.path.join(AUDIO_DIR, mixed_filename)
        
        noise_color = valid_noises[noise_type]
        # 生成长噪音（600秒足够覆盖任何对话），amix=duration=first 自动截断到对话长度
        cmd = [
            FFMPEG, "-y",
            "-i", merged_path,
            "-f", "lavfi", "-i", f"anoisesrc=a={noise_volume}:d=600:c={noise_color}",
            "-filter_complex", f"[1:a]volume={noise_volume}[noise];[0:a][noise]amix=inputs=2:duration=first:dropout_transition=0",
            "-acodec", "libmp3lame", "-q:a", "2",
            mixed_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # 清理临时文件
            for f in files + padded_files:
                try: os.remove(f)
                except: pass
            try: os.remove(merged_path)
            except: pass
            raise RuntimeError(f"ffmpeg噪音叠加失败: {result.stderr}")
        
        # 删除旧的合并文件，返回叠加噪音后的新文件
        try: os.remove(merged_path)
        except: pass
        return mixed_filename
    
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
    """对话模式：多段文本合并成一个音频，可选叠加环境噪音"""
    data = request.get_json()
    segments = data.get("segments", [])
    noise_type = data.get("noise", "none")
    noise_volume = data.get("noise_volume", 0.3)
    real_mode = data.get("real_mode", False)
    
    if not segments or not isinstance(segments, list):
        return jsonify({"success": False, "error": "请提供对话分段数据"}), 400
    
    # 检查总字数
    total_chars = sum(len(seg.get("text", "")) for seg in segments)
    if total_chars > 5000:
        return jsonify({"success": False, "error": "文本过长，总字数请控制在 5000 字以内"}), 400
    
    # 校验噪音参数
    if noise_type not in ("none", "white", "pink", "brown"):
        return jsonify({"success": False, "error": "不支持的噪音类型"}), 400
    try:
        noise_volume = float(noise_volume)
        if not (0 <= noise_volume <= 1):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "噪音音量需在 0~1 之间"}), 400
    
    try:
        filename = asyncio.run(generate_dialogue(segments, noise_type, noise_volume, real_mode))
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
