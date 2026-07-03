import os
import json

def align_subtitles(tts_audio_path: str, hinglish_text: str) -> list:
    """
    Distributes the Hinglish words evenly across the duration of the audio clip.
    This avoids loading the heavy Whisper model, saving RAM and CPU.
    """
    import subprocess
    print(f"Timing alignment (even distribution) for: {tts_audio_path}")
    
    hinglish_words = [w.strip() for w in hinglish_text.split() if w.strip()]
    aligned = []
    num_hinglish = len(hinglish_words)
    
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", tts_audio_path]
    try:
        dur = float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        dur = 5.0
        
    step = dur / max(1, num_hinglish)
    for i, word in enumerate(hinglish_words):
        aligned.append({
            "word": word,
            "start": i * step,
            "end": (i + 1) * step
        })
    return aligned

def generate_ass_subtitles(aligned_scenes: list, car_details: list, output_path: str):
    """
    Generates an Advanced SubStation Alpha (.ass) file with:
    1. Small, fast-updating bottom-center captions (chunked to 3 words max) with fade animations.
    2. Styled persistent top-left info cards for each car segment showing model, spec, km, and price.
    """
    ass_template = """[Script Info]
Title: Car Ad Karaoke Subtitles
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Impact,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,3,2,2,40,40,150,1
Style: InfoCard,Arial Black,34,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,1,0,7,40,40,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    
    def format_ass_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds % 1) * 100)
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    # 1. Generate persistent top-left info cards for each car
    for car in car_details:
        start_s = float(car.get("visual_timestamps", {}).get("start", 0.0))
        end_s = float(car.get("visual_timestamps", {}).get("end", start_s + 5.0))
        
        model = car.get("model", "Premium SUV")
        color = car.get("color", "Original")
        specs = car.get("specs", "")
        # Clean specs list
        specs_parts = [p.strip() for p in specs.split(",") if p.strip()]
        year_info = specs_parts[0] if specs_parts else "2021"
        fuel_info = specs_parts[1] if len(specs_parts) > 1 else "Premium"
        
        km = car.get("odometer_value", "Checked")
        price = car.get("price_value", "Negotiable")
        if price.lower() in ["low", "high", "none", "unknown", "negotiable"]:
            price = "Best Offer"
            
        # Format the card content
        card_text = (
            f"{{\\fad(250,250)}}{{\\c&H00FFFF&}}{model} ({color})\\N"
            f"{{\\c&HFFFFFF&}}{year_info} | {fuel_info} | {km}\\N"
            f"{{\\c&H00FF00&}}Price: {price}"
        )
        
        events.append(
            f"Dialogue: 1,{format_ass_time(start_s)},{format_ass_time(end_s)},InfoCard,,0,0,0,,{card_text}"
        )

    # 2. Generate bottom captions chunked into max 3 words
    for scene in aligned_scenes:
        scene_start = scene["scene_start"]
        scene_aligned_words = scene["words"]
        
        chunk_size = 3
        for idx in range(0, len(scene_aligned_words), chunk_size):
            chunk = scene_aligned_words[idx:idx+chunk_size]
            if not chunk:
                continue
                
            chunk_start = chunk[0]["start"] + scene_start
            chunk_end = chunk[-1]["end"] + scene_start
            
            subtitle_text_parts = []
            for word_info in chunk:
                w_start = word_info["start"] + scene_start
                w_end = word_info["end"] + scene_start
                w_dur_cs = int((w_end - w_start) * 100)
                if w_dur_cs <= 0:
                    w_dur_cs = 10
                
                is_number = any(char.isdigit() for char in word_info["word"]) or word_info["word"].lower() in ["lakh", "lakhs", "cr", "crore"]
                if is_number:
                    subtitle_text_parts.append(f"{{\\k{w_dur_cs}}}{{\\c&H00FFFF&}}{word_info['word']}{{\\c&HFFFFFF&}}")
                else:
                    subtitle_text_parts.append(f"{{\\k{w_dur_cs}}}{word_info['word']}")
                    
            line_text = "{\\fad(80,80)}" + " ".join(subtitle_text_parts)
            events.append(
                f"Dialogue: 0,{format_ass_time(chunk_start)},{format_ass_time(chunk_end)},Default,,0,0,0,,{line_text}"
            )
            
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_template + "\n".join(events))
    print(f"ASS subtitle file written to {output_path}")
