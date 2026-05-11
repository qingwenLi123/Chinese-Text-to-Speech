"""
中文文本转语音 (TTS)
基于微软 Edge TTS，免费、高质量
支持男声和女声
"""

import asyncio
import edge_tts
import sys

# 中文语音配置
VOICES = {
    "male": "zh-CN-YunjianNeural",      # 男声
    "female": "zh-CN-XiaoxiaoNeural",   # 女声
}


async def text_to_speech(text: str, voice_type: str, output_file: str) -> None:
    """
    将文本转换为语音并保存为音频文件
    
    参数:
        text: 要转换的中文文本
        voice_type: "male" 或 "female"
        output_file: 输出音频文件路径（建议 .mp3）
    """
    if voice_type not in VOICES:
        raise ValueError(f"不支持的音色: {voice_type}，请使用 'male' 或 'female'")
    
    voice = VOICES[voice_type]
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)
    print(f"[OK] 已生成音频: {output_file} | 音色: {'男声' if voice_type == 'male' else '女声'}")


def convert_text(text: str, voice_type: str = "female", output_file: str = "output.mp3") -> None:
    """
    同步调用文本转语音（方便在其他代码中调用）
    
    示例:
        convert_text("你好，这是一个测试", voice_type="male", output_file="test.mp3")
    """
    asyncio.run(text_to_speech(text, voice_type, output_file))


async def preview_voices(sample_text: str = "你好，这是中文语音合成测试。") -> None:
    """
    生成两个音色的试听文件
    """
    print(f"正在生成试听音频，文本: {sample_text}")
    await text_to_speech(sample_text, "male", "preview_male.mp3")
    await text_to_speech(sample_text, "female", "preview_female.mp3")
    print("试听文件已生成: preview_male.mp3, preview_female.mp3")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="中文文本转语音工具")
    parser.add_argument("text", nargs="?", help="要转换的中文文本")
    parser.add_argument("-v", "--voice", choices=["male", "female"], default="female", help="选择音色: male(男声) / female(女声)")
    parser.add_argument("-o", "--output", default="output.mp3", help="输出文件名 (默认: output.mp3)")
    parser.add_argument("--preview", action="store_true", help="生成两个音色的试听文件")
    
    args = parser.parse_args()
    
    if args.preview:
        asyncio.run(preview_voices())
    elif args.text:
        asyncio.run(text_to_speech(args.text, args.voice, args.output))
    else:
        # 默认演示
        print("中文TTS演示 - 生成两个音色的测试音频...")
        asyncio.run(preview_voices("你好，欢迎使用中文文本转语音功能。"))
        print("\n使用方式:")
        print("  python tts.py '你好世界' -v male -o hello.mp3")
        print("  python tts.py -v female '今天天气不错' -o weather.mp3")
        print("  python tts.py --preview")
