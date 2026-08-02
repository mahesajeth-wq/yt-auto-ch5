import os
import json
import random
import subprocess
import requests
import base64
import urllib.parse
from pipeline.config import THUMBNAIL_LAYOUTS

_LAYOUT_STATE_FILE = "thumbnail_state.json"

def _load_last_layout() -> str | None:
    if os.path.exists(_LAYOUT_STATE_FILE):
        try:
            with open(_LAYOUT_STATE_FILE) as f:
                return json.load(f).get("last_layout")
        except Exception:
            return None
    return None

def _save_last_layout(layout: str):
    with open(_LAYOUT_STATE_FILE, "w") as f:
        json.dump({"last_layout": layout}, f)

def clean_thumbnail_text(text: str) -> str:
    cleaned = "".join(c for c in text if c.isalnum() or c in " -!?")
    return cleaned.replace("'", "'\\\\''")

def _generate_gemini_bg_image(prompt: str) -> bytes | None:
    """Attempt to generate background thumbnail using Gemini API Key (Nano Banana / Flash Image)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
        
    models = ["gemini-2.5-flash-image", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image"]
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": f"Generate a high-resolution 16:9 cinematic YouTube thumbnail background image for: {prompt}. High contrast, 4k ultra detailed."}]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE"]
            }
        }
        try:
            r = requests.post(url, json=payload, timeout=25)
            if r.status_code == 200:
                data = r.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for p in parts:
                        inline = p.get("inlineData") or p.get("inline_data")
                        if inline and "data" in inline:
                            print(f"[Thumbnail] Successfully generated AI background via Gemini model {m}!")
                            return base64.b64decode(inline["data"])
        except Exception as e:
            print(f"[Thumbnail] Gemini model {m} call error: {e}")
    return None

def _generate_pollinations_bg_image(prompt: str) -> bytes | None:
    """Fallback high-availability image generator via Pollinations AI (Flux/Imagen)."""
    try:
        clean_p = f"{prompt} cinematic 4k youtube thumbnail background high contrast"
        encoded = urllib.parse.quote(clean_p)
        seed = random.randint(1, 99999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1280&height=720&model=flux&nologo=true&seed={seed}"
        r = requests.get(url, timeout=25)
        if r.status_code == 200 and len(r.content) > 10000:
            print("[Thumbnail] Successfully generated AI background via Pollinations AI (Flux)!")
            return r.content
    except Exception as e:
        print(f"[Thumbnail] Pollinations AI generator error: {e}")
    return None

def _build_filter(layout: str, cleaned_text: str) -> str:
    """Return an FFmpeg -vf filter string for the given layout."""
    text_color = random.choice(["#FFDD00", "#FF2D55", "#00C7FC", "#FFFFFF", "#FF9500"])
    shadow = "shadowcolor=black@0.55:shadowx=6:shadowy=6"
    
    if layout == "dark_top_bar":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=0:w=iw:h=190:color=black@0.75:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=105:"
            f"fontcolor='{text_color}':borderw=6:bordercolor=black:{shadow}:x=(w-text_w)/2:y=65"
        )
    elif layout == "centered_gradient":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=210:w=iw:h=300:color=black@0.65:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=115:"
            f"fontcolor='{text_color}':borderw=8:bordercolor=black:{shadow}:x=(w-text_w)/2:y=(h-text_h)/2"
        )
    elif layout == "bottom_third":
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=490:w=iw:h=230:color=black@0.80:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=100:"
            f"fontcolor='{text_color}':borderw=6:bordercolor=black:{shadow}:x=(w-text_w)/2:y=535"
        )
    else:  # split_left
        return (
            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
            f"drawbox=x=0:y=0:w=560:h=ih:color=black@0.75:t=fill,"
            f"drawtext=text='{cleaned_text}':font='Bebas Neue':fontsize=90:"
            f"fontcolor='{text_color}':borderw=5:bordercolor=black:{shadow}:x=40:y=(h-text_h)/2"
        )

def _generate_vivid_thumbnail_prompt(topic: str, thumbnail_text: str) -> str:
    """Uses Gemini to transform abstract topics into concrete, high-impact 16:9 visual prompts."""
    try:
        from pipeline.gemini import GeminiClient
        client = GeminiClient()
        prompt = (
            f"Topic: '{topic}'\n"
            f"Thumbnail Text: '{thumbnail_text}'\n\n"
            f"You are a top YouTube thumbnail designer. Write a 1-sentence, highly descriptive visual image prompt for an AI image generator (Flux/Midjourney).\n"
            f"CRITICAL RULES:\n"
            f"1. Describe a CONCRETE 3D visual scene or subject (e.g. 'A glowing blue quantum computer core radiating energy bolts inside a dark futuristic laboratory').\n"
            f"2. Include dramatic lighting, vivid contrasting colors (neon yellow, electric blue, crimson red), and 8k cinematic details.\n"
            f"3. NEVER include text, words, letters, or quotes inside the image prompt.\n"
            f"Return ONLY the image prompt text."
        )
        res = client.generate_text(prompt, max_tokens=100)
        clean = res.strip().replace('"', '').replace('\n', ' ')
        for prefix in ["Here is a prompt:", "Here is the prompt:", "Here is a descriptive", "Here is the visual prompt:", "Here is a", "Here is the"]:
            if clean.lower().startswith(prefix.lower()):
                clean = clean[len(prefix):].strip()
        if len(clean) > 10:
            print(f"[Thumbnail] Generated visual prompt: '{clean}'")
            return clean
    except Exception as e:
        print(f"[Thumbnail] Visual prompt generation failed: {e}")
    return f"Cinematic 4k high contrast YouTube thumbnail background for {topic or thumbnail_text}"

def generate_thumbnail(final_video_path: str, thumbnail_text: str, topic_prompt: str = "") -> str:
    print(f"Generating thumbnail for '{thumbnail_text}'...")
    os.makedirs("output", exist_ok=True)

    hook_frame_path = "output/hook_frame.jpg"
    thumbnail_path  = "output/thumbnail.jpg"

    # 1. Generate dedicated visual prompt for AI image generator
    vivid_prompt = _generate_vivid_thumbnail_prompt(topic_prompt, thumbnail_text)

    # 2. Try Gemini API key first (Nano Banana / Flash Image)
    bg_bytes = _generate_gemini_bg_image(vivid_prompt)
    
    # 3. Fallback to Pollinations AI generator if Gemini rate-limited
    if not bg_bytes:
        bg_bytes = _generate_pollinations_bg_image(vivid_prompt)
        
    # 3. If AI image generation succeeded, save to hook_frame_path
    if bg_bytes:
        with open(hook_frame_path, "wb") as f:
            f.write(bg_bytes)
    else:
        # 4. Fallback: Extract best frame from final video
        print("[Thumbnail] Falling back to video frame extraction...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", final_video_path,
             "-vf", "thumbnail=n=300", "-frames:v", "1", "-q:v", "2", hook_frame_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    # 5. Pick layout — avoid repeating last one
    last = _load_last_layout()
    available = [l for l in THUMBNAIL_LAYOUTS if l != last]
    if not available:
        available = THUMBNAIL_LAYOUTS
    layout = random.choice(available)
    print(f"[Thumbnail] Layout: {layout}")

    cleaned = clean_thumbnail_text(thumbnail_text).upper()
    vf = _build_filter(layout, cleaned)

    # 6. Burn high-impact typography
    cmd = ["ffmpeg", "-y", "-i", hook_frame_path, "-vf", vf, "-q:v", "2", thumbnail_path]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("Bebas Neue failed, retrying with DejaVu Sans Bold...")
        vf_fallback = vf.replace("font='Bebas Neue':fontsize=105", "font='DejaVu Sans Bold':fontsize=85")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=115", "font='DejaVu Sans Bold':fontsize=95")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=100", "font='DejaVu Sans Bold':fontsize=80")
        vf_fallback = vf_fallback.replace("font='Bebas Neue':fontsize=90", "font='DejaVu Sans Bold':fontsize=75")
        cmd_fb = ["ffmpeg", "-y", "-i", hook_frame_path, "-vf", vf_fallback, "-q:v", "2", thumbnail_path]
        subprocess.run(cmd_fb, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    _save_last_layout(layout)
    print(f"Thumbnail generated: {thumbnail_path}")
    return thumbnail_path
