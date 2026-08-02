
def download_font(font_name: str, url: str):
    import urllib.request
    import os
    import subprocess
    
    font_dir = os.path.expanduser("~/.local/share/fonts")
    os.makedirs(font_dir, exist_ok=True)
    
    filename = url.split("/")[-1]
    font_path = os.path.join(font_dir, filename)
    
    if not os.path.exists(font_path):
        print(f"[Font] Downloading {font_name} from {url}...")
        try:
            urllib.request.urlretrieve(url, font_path)
            subprocess.run(["fc-cache", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[Font] Installed {font_name} successfully.")
        except Exception as e:
            print(f"[Font] Failed to download {font_name}: {e}")

import os
import soundfile as sf

POWER_WORDS = {"TRUTH", "SECRET", "SHOCKING", "DANGEROUS", "CRITICAL", "BRUTAL", "SURPRISING", "REVEALED", "WARNING", "CAUTION", "ACCIDENT", "CRASH", "SAFE", "SAFETY", "IMPOSSIBLE", "MYSTERY", "KILL", "DIED", "ALIVE", "DEATH", "BANNED", "PROVEN", "HIDDEN", "DESTROYED", "BREAKTHROUGH", "DISCOVERY", "UNCOVERED", "LIE", "LIES"}

def fmt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def align_words(script_words: list[str], whisper_words: list[dict]) -> list[dict]:
    aligned = []
    ns = len(script_words)
    nw = len(whisper_words)
    if ns == 0:
        return []
    if nw == 0:
        return []
    w_idx = 0
    for s_idx, s_word in enumerate(script_words):
        best_w_idx = w_idx
        best_score = 0
        for candidate_idx in range(max(0, w_idx - 4), min(nw, w_idx + 15)):
            w_word = whisper_words[candidate_idx]["text"].strip(".,!?\"'()").upper()
            s_word_clean = s_word.strip(".,!?\"'()").upper()
            if w_word == s_word_clean:
                score = 3
            elif w_word in s_word_clean or s_word_clean in w_word:
                score = 2
            else:
                score = 0
            if score > best_score:
                best_score = score
                best_w_idx = candidate_idx
        if best_score > 0:
            w_idx = best_w_idx
        clamped_w_idx = min(max(0, w_idx), nw - 1)
        aligned.append({
            "word": s_word,
            "start": whisper_words[clamped_w_idx]["start"],
            "end": whisper_words[clamped_w_idx]["end"]
        })
        w_idx = clamped_w_idx + 1
    return aligned

def generate_captions(audio_files: list[str], script: dict, format_type: str = "short") -> str:
    if format_type == "short":
        play_res_x = 1080
        play_res_y = 1920
        font_size  = 88      # was 72 — larger for mobile screens
        margin_v   = 420     # position in lower-middle area of short
    else:
        play_res_x = 1920
        play_res_y = 1080
        font_size  = 60      # was 54
        margin_v   = 130

    pos_x = play_res_x // 2
    pos_y = play_res_y - margin_v
    pos_tag = f"{{\\pos({pos_x},{pos_y})}}"

    ass_events = []
    ass_events = []
    time_offset = 0.0

    model = None
    try:
        from faster_whisper import WhisperModel
        print("Loading faster-whisper 'base' model on CPU...")
        model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=1, num_workers=1)
    except Exception as model_err:
        print(f"Warning: Could not load faster-whisper model ({model_err}). Will use rule-based timing.")

    for i, (audio_path, seg) in enumerate(zip(audio_files, script["segments"])):
        if not os.path.exists(audio_path):
            print(f"Warning: Audio file missing: {audio_path}. Generating fallback audio...")
            try:
                from gtts import gTTS
                tts = gTTS(text=seg["narration"], lang='en')
                temp_mp3 = f"output/temp_missing_{i}.mp3"
                tts.save(temp_mp3)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", temp_mp3, "-ac", "1", "-ar", "24000", audio_path],
                    capture_output=True, check=True
                )
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
            except Exception as e:
                print(f"Failed to generate fallback TTS for missing {audio_path}: {e}")

        # Get accurate duration with soundfile + ffprobe fallback
        duration = 5.0
        try:
            data, sr = sf.read(audio_path)
            duration = len(data) / sr
        except Exception as sf_err:
            try:
                res = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                    capture_output=True, text=True, check=True
                )
                duration = float(res.stdout.strip())
            except Exception:
                print(f"Warning: Could not read duration for {audio_path} ({sf_err}). Defaulting to 5.0s.")

        script_words = seg["narration"].split()
        aligned_words = []

        if model is not None:
            try:
                print(f"Transcribing TTS file: {audio_path}...")
                segments_out, info = model.transcribe(audio_path, word_timestamps=True)
                whisper_words = []
                for whisper_seg in segments_out:
                    if whisper_seg.words:
                        for word_info in whisper_seg.words:
                            w_text = word_info.word.strip()
                            if w_text:
                                whisper_words.append({
                                    "text": w_text,
                                    "start": word_info.start,
                                    "end": word_info.end
                                })
                aligned_words = align_words(script_words, whisper_words)
            except Exception as seg_err:
                print(f"Warning: Whisper failed for segment {seg['id']} ({seg_err}). Using rule-based timing.")

        # Fallback to even distribution if Whisper was unavailable, failed, or produced empty alignment
        if not aligned_words and script_words:
            word_dur = duration / len(script_words)
            for w_idx, word in enumerate(script_words):
                aligned_words.append({
                    "word": word,
                    "start": w_idx * word_dur,
                    "end": (w_idx + 1) * word_dur
                })

        # Generate sliding window subtitles
        for idx, word_info in enumerate(aligned_words):
            start = time_offset + word_info["start"]
            end = time_offset + word_info["end"]

            start_win = max(0, idx - 1)
            end_win = min(len(aligned_words), idx + 2)

            word_dur_ms = int((word_info["end"] - word_info["start"]) * 1000)
            pop_end = min(70, max(20, int(word_dur_ms * 0.45)))
            settle_end = min(150, max(40, word_dur_ms))

            styled_parts = []
            for w_idx in range(start_win, end_win):
                curr_word = aligned_words[w_idx]["word"]
                curr_word_clean = curr_word.strip(".,!?\"'()").upper()
                if w_idx == idx:
                    if curr_word_clean in POWER_WORDS:
                        styled_parts.append(
                            f"{{\\fscx90\\fscy90\\t(0,{pop_end},1.2,\\fscx122\\fscy122)"
                            f"\\t({pop_end},{settle_end},1,\\fscx100\\fscy100)\\c&H0033FF33&}}{curr_word.upper()}{{\\r}}"
                        )
                    else:
                        styled_parts.append(
                            f"{{\\fscx90\\fscy90\\t(0,{pop_end},1.2,\\fscx112\\fscy112)"
                            f"\\t({pop_end},{settle_end},1,\\fscx100\\fscy100)\\c&H0000E5FF&}}{curr_word.upper()}{{\\r}}"
                        )
                else:
                    styled_parts.append(f"{{\\c&HFFFFFF&}}{curr_word.upper()}{{\\r}}")

            styled_text = " ".join(styled_parts)
            ass_events.append(f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},Default,,0,0,0,,{pos_tag}{styled_text}")

        time_offset += duration
        print(f"Segment {seg['id']} duration: {duration:.2f}s, Cumulative offset: {time_offset:.2f}s")
        
    # Dynamic ASS subtitle configuration based on format
    fonts_pool = [
        ("Bebas Neue", "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf"),
        ("Anton", "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"),
        ("Oswald", "https://github.com/google/fonts/raw/main/ofl/oswald/static/Oswald-Bold.ttf"),
        ("Montserrat ExtraBold", "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"),
        ("Archivo Black", "https://github.com/google/fonts/raw/main/ofl/archivoblack/ArchivoBlack-Regular.ttf")
    ]
    import random
    picked_font, picked_url = random.choice(fonts_pool)
    try:
        download_font(picked_font, picked_url)
        script["font_name"] = picked_font
        print(f"[Font] Picked and installed font: {picked_font}")
    except Exception as e:
        picked_font = "Bebas Neue"
        script["font_name"] = "Bebas Neue"
        print(f"[Font] Error downloading, falling back to Bebas Neue: {e}")

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{picked_font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,8,2,2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    os.makedirs("output", exist_ok=True)
    ass_path = "output/captions.ass"
    with open(ass_path, "w") as f:
        f.write(ass_header)
        f.write("\n".join(ass_events))
        f.write("\n")
        
    print(f"Generated ASS captions saved to {ass_path}")
    return ass_path
