import json
from autopilot.core.media_inspection import inspect_media

report = json.load(open('production_5_videos_report.json', encoding='utf-8'))
for item in report:
    probe = inspect_media(item['final_path'])
    item['video_codec'] = probe.get('video', {}).get('codec_name', 'h264')
    item['audio_codec'] = probe.get('audio', {}).get('codec_name', 'aac')
    item['fps'] = probe.get('video', {}).get('avg_frame_rate')

with open('production_5_videos_report.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2)

for item in report:
    print(f"[{item['index']}/5] {item['category'].upper()}: {item['topic']}")
    print(f"   File: {item['final_path']}")
    print(f"   Size: {item['file_size_mb']} MB, Duration: {item['duration_sec']}s, Resolution: {item['resolution']}, Codec: {item['video_codec']}/{item['audio_codec']}, FPS: {item['fps']}")
    print(f"   QA Status: {item['qa_status']}, Publish Allowed: {item['publish_allowed']}")
    print(f"   YouTube: {item['youtube_url']} (ID: {item['youtube_video_id']}, Privacy: {item['privacy_status']})")
    print(f"   DB Publication ID: {item['db_publication_id']}\n")
