"""Local fixture generator for M3 deterministic asset provider."""
from PIL import Image, ImageDraw, ImageFont
import os

FIXTURES_DIR = os.path.dirname(__file__)

def make_test_image(name="fixture_image.png", size=(640, 360), text="Fixture"):
    img = Image.new("RGB", size, (30, 60, 90))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    draw.text((20, size[1]//2 - 10), text, fill=(255, 255, 255), font=font)
    path = os.path.join(FIXTURES_DIR, name)
    img.save(path)
    return path

def make_test_video(name="fixture_video.mp4", duration=2):
    import subprocess
    out = os.path.join(FIXTURES_DIR, name)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=720x1280:rate=25",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        out,
    ]
    subprocess.run(cmd, capture_output=True)
    return out
