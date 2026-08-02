import os
import random
import json
import wave
import subprocess

from pipeline.config import GEMINI_VOICES, KOKORO_VOICES
from pipeline.gemini import GeminiClient

STATE_PATH = "voice_state.json"

def pick_voice(pool: list[str], state_key: str) -> str:
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                state = json.load(f)
        except Exception:
            pass
    last = state.get(state_key)
    choice = random.choice([v for v in pool if v != last] or pool)
    state[state_key] = choice
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Warning: Failed to write voice state: {e}")
    return choice

def get_wav_duration(filepath: str) -> float:
    with wave.open(filepath, 'rb') as f:
        frames = f.getnframes()
        rate = f.getframerate()
        return frames / float(rate)

def split_combined_audio(combined_path: str, segments: list[dict]):
    import subprocess
    # First, try Whisper word alignment
    try:
        from faster_whisper import WhisperModel
        print("[TTS] Loading faster-whisper 'base' model on CPU for segmentation...")
        model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=1, num_workers=1)
        segments_out, info = model.transcribe(combined_path, word_timestamps=True)
        
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
        
        # Build script words list and map word indices back to segments
        script_words = []
        seg_word_counts = []
        for seg in segments:
            words = seg["narration"].split()
            script_words.extend(words)
            seg_word_counts.append(len(words))
            
        aligned_words = []
        ns = len(script_words)
        nw = len(whisper_words)
        if ns > 0 and nw > 0:
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
                aligned_words.append({
                    "word": s_word,
                    "start": whisper_words[clamped_w_idx]["start"],
                    "end": whisper_words[clamped_w_idx]["end"]
                })
                w_idx = clamped_w_idx + 1
        
        if len(aligned_words) == len(script_words):
            word_offset = 0
            total_duration = get_wav_duration(combined_path)
            
            # 1. Gather raw word starts/ends for each segment
            seg_bounds = []
            for i, seg in enumerate(segments):
                num_words = seg_word_counts[i]
                seg_words = aligned_words[word_offset : word_offset + num_words]
                word_offset += num_words
                
                if seg_words:
                    seg_bounds.append((seg_words[0]["start"], seg_words[-1]["end"]))
                else:
                    # fallback if segment is empty
                    seg_bounds.append((total_duration, total_duration))
                    
            # 2. Calculate continuous slice boundaries (midpoints during silences)
            slice_starts = []
            slice_ends = []
            
            for i in range(len(segments)):
                if i == 0:
                    start_time = 0.0
                else:
                    # Midpoint between previous segment's end and this segment's start
                    # Prevents cutting off trailing reverb/breath and preserves natural gaps
                    start_time = (seg_bounds[i-1][1] + seg_bounds[i][0]) / 2.0
                    
                if i == len(segments) - 1:
                    end_time = total_duration
                else:
                    end_time = (seg_bounds[i][1] + seg_bounds[i+1][0]) / 2.0
                    
                slice_starts.append(start_time)
                slice_ends.append(end_time)
            
            # 3. Perform slicing with soundfile (sample-accurate, zero FFmpeg bugs)
            import soundfile as sf
            data, sr = sf.read(combined_path)
            total_samples = len(data)

            for i, seg in enumerate(segments):
                start_time = slice_starts[i]
                end_time = slice_ends[i]
                
                start_sample = max(0, int(start_time * sr))
                end_sample = min(total_samples, int(end_time * sr))
                if end_sample <= start_sample + int(0.2 * sr):
                    end_sample = min(total_samples, start_sample + int(0.5 * sr))
                    
                out_path = f"output/tts_segment_{seg['id']}.wav"
                print(f"[TTS] Slicing Segment {seg['id']}: {start_time:.3f}s -> {end_time:.3f}s ({end_sample - start_sample} samples)")
                sf.write(out_path, data[start_sample:end_sample], sr)
            return
    except Exception as e:
        print(f"[TTS] Word alignment split failed: {e}. Falling back to proportional split.")
        
    # Proportional split fallback
    import soundfile as sf
    data, sr = sf.read(combined_path)
    total_samples = len(data)
    total_duration = len(data) / sr
    weights = [len(seg["narration"]) for seg in segments]
    total_weight = sum(weights) if sum(weights) > 0 else 1
    
    current_time = 0.0
    for i, seg in enumerate(segments):
        duration = total_duration * (weights[i] / total_weight)
        end_time = current_time + duration
        if i == len(segments) - 1:
            end_time = total_duration
            
        start_sample = max(0, int(current_time * sr))
        end_sample = min(total_samples, int(end_time * sr))
        if end_sample <= start_sample + int(0.2 * sr):
            end_sample = min(total_samples, start_sample + int(0.5 * sr))
            
        out_path = f"output/tts_segment_{seg['id']}.wav"
        print(f"[TTS] Proportional slicing Segment {seg['id']}: {current_time:.3f}s -> {end_time:.3f}s ({end_sample - start_sample} samples)")
        sf.write(out_path, data[start_sample:end_sample], sr)
        current_time = end_time

def generate_audio(script: dict) -> list[str]:
    """
    Generates TTS for all segments using a SINGLE voice for the whole video.
    To ensure perfect voice tone consistency and prevent shifting depth/pitch,
    we generate the entire voiceover script as a SINGLE combined audio file, 
    then split it back into segment files using word-level alignment (Whisper).
    """
    gemini_client = GeminiClient()
    os.makedirs("output", exist_ok=True)

    gemini_voice = pick_voice(GEMINI_VOICES, "gemini")
    ko_voice     = pick_voice(KOKORO_VOICES, "kokoro")

    segments = script["segments"]
    combined_raw_path = "output/tts_combined_raw.wav"
    
    # Clean up any previously generated segment files to prevent stale state
    for seg in segments:
        p = f"output/tts_segment_{seg['id']}.wav"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # We join segments with a period and newline for natural pauses between sentences
    full_text = "\n\n".join(seg["narration"] for seg in segments)

    # ── Pass 1: Try Gemini combined ──────────────────────────────────────────
    print(f"[TTS] Using Gemini voice '{gemini_voice}' for this video.")
    gemini_failed = False

    try:
        vocal_tone = script.get("vocal_tone")
        voiceover_plan = script.get("voiceover_plan")
        
        audio_bytes, mime_type = gemini_client.generate_tts(
            full_text,
            voice=gemini_voice,
            vocal_tone=vocal_tone,
            voiceover_plan=voiceover_plan
        )
        
        if audio_bytes.startswith(b"RIFF") or "wav" in mime_type.lower():
            with open(combined_raw_path, "wb") as wf:
                wf.write(audio_bytes)
        else:
            with wave.open(combined_raw_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
        print(f"[TTS] Gemini combined generated successfully.")
    except Exception as e:
        print(f"[TTS] Gemini combined failed: {e}")
        gemini_failed = True

    if not gemini_failed:
        try:
            split_combined_audio(combined_raw_path, segments)
            return [f"output/tts_segment_{seg['id']}.wav" for seg in segments]
        except Exception as split_err:
            print(f"[TTS] Split combined audio failed: {split_err}")
            gemini_failed = True

    # ── Pass 2: Gemini combined splitting failed. Fallback to per-segment TTS ──
    print(f"[TTS] Fallback: Generating TTS per-segment directly...")
    audio_files = []
    for seg in segments:
        seg_id = seg["id"]
        out_path = f"output/tts_segment_{seg_id}.wav"
        text = seg["narration"]
        
        # Try per-segment Gemini TTS
        generated = False
        try:
            audio_bytes, mime_type = gemini_client.generate_tts(
                text,
                voice=gemini_voice,
                vocal_tone=script.get("vocal_tone"),
                voiceover_plan=None
            )
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
            generated = True
        except Exception as e:
            print(f"[TTS] Per-segment Gemini failed for segment {seg_id}: {e}")

        # Fallback to gTTS if per-segment Gemini fails
        if not generated:
            try:
                from gtts import gTTS
                tts = gTTS(text=text, lang='en')
                temp_mp3 = f"output/temp_tts_{seg_id}.mp3"
                tts.save(temp_mp3)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", temp_mp3, "-ac", "1", "-ar", "24000", out_path],
                    capture_output=True, check=True
                )
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
                generated = True
            except Exception as g_err:
                print(f"[TTS] gTTS fallback failed for segment {seg_id}: {g_err}")

        audio_files.append(out_path)

    return audio_files
