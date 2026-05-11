import edge_tts

voices = edge_tts.list_voices_sync()
for v in voices:
    if 'zh-CN' in v['ShortName']:
        name = v['ShortName']
        gender = v.get('Gender', 'Unknown')
        friendly = v.get('FriendlyName', '')
        print(f"{name} | {gender} | {friendly}")
