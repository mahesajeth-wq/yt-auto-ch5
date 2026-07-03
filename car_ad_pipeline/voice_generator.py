import os
import json
import time
import subprocess
from car_ad_pipeline.gemini_client import GeminiClient

def _pcm_to_wav(pcm_data: bytes, wav_path: str, sample_rate: int = 24000):
    """Convert raw PCM (s16le, mono, 24kHz) bytes to a proper WAV file via ffmpeg."""
    pcm_path = wav_path + ".pcm"
    with open(pcm_path, "wb") as f:
        f.write(pcm_data)
    cmd = [
        "ffmpeg", "-y",
        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
        "-i", pcm_path,
        wav_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.remove(pcm_path)

def generate_voiceover(client: GeminiClient, scene_cues: list, output_dir: str) -> list:
    print("Generating voiceover audio segments using Gemini TTS...")
    os.makedirs(output_dir, exist_ok=True)
    
    audio_paths = []
    total_prompt_tokens = 0
    total_output_tokens = 0
    
    token_log_path = os.path.join(output_dir, "token_usage.log")
    
    for i, scene in enumerate(scene_cues):
        ad_copy = scene.get("ad_copy_hindi", "").strip()
        if not ad_copy:
            print(f"Scene {i+1} has no Hindi ad copy. Skipping voice generation.")
            audio_paths.append(None)
            continue
            
        audio_filename = f"scene_{i+1}_tts.wav"
        audio_path = os.path.join(output_dir, audio_filename)
        
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            print(f"Voiceover for Scene {i+1} already exists at {audio_path}, skipping generation.")
            audio_paths.append(audio_path)
            continue
            
        print(f"Generating TTS for Scene {i+1}: '{ad_copy}'...")
        
        audio_data = client.generate_tts(ad_copy, voice="Aoede")
        
        # Log token usage estimation
        prompt_est = len(ad_copy) // 2
        output_est = len(audio_data) // 800
        
        total_prompt_tokens += prompt_est
        total_output_tokens += output_est
        
        audio_filename = f"scene_{i+1}_tts.wav"
        audio_path = os.path.join(output_dir, audio_filename)
        
        # Convert raw PCM to proper WAV
        _pcm_to_wav(audio_data, audio_path)
            
        print(f"Saved: {audio_path}")
        audio_paths.append(audio_path)
        
    # Append to token usage log
    log_line = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Car Ad Pipeline - "
        f"Prompt tokens (est): {total_prompt_tokens}, Output tokens (est): {total_output_tokens}, "
        f"Estimated Audio cost: ${(total_output_tokens / 1000000) * 20:.5f}\n"
    )
    with open(token_log_path, "a") as f:
        f.write(log_line)
        
    print(f"Logged token usage to {token_log_path}: {log_line.strip()}")
    return audio_paths
