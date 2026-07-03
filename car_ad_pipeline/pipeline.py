import os
import sys
import json
import time

from car_ad_pipeline.gemini_client import GeminiClient
from car_ad_pipeline.transcriber import extract_audio, transcribe_audio
from car_ad_pipeline.script_generator import generate_script
from car_ad_pipeline.price_lookup import lookup_market_price
from car_ad_pipeline.voice_generator import generate_voiceover
from car_ad_pipeline.subtitle_generator import align_subtitles, generate_ass_subtitles
from car_ad_pipeline.video_editor import compile_ad

def run_pipeline(video_path: str, output_dir: str, resume: bool = False):
    print(f"Starting Car Ad Video Pipeline for {video_path}...")
    start_time = time.time()
    
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Step 1: Initialize client
    client = GeminiClient()
    
    script_output_path = os.path.join(output_dir, "ad_script.json")
    
    if resume and os.path.exists(script_output_path):
        print("\n--- RESUME MODE: Loading existing ad_script.json ---")
        with open(script_output_path, "r", encoding="utf-8") as f:
            ad_script = json.load(f)
        print(f"Loaded script with {len(ad_script.get('scene_cues', []))} scenes.")
    else:
        # Step 2c: Transcribe original audio (as timing anchor)
        print("\n--- STEP 2c: Extracting & Transcribing Audio Anchor ---")
        audio_path = os.path.join(temp_dir, "original_audio.wav")
        transcript_path = os.path.join(temp_dir, "original_transcript.json")
        
        audio_transcript = None
        try:
            extract_audio(video_path, audio_path)
            audio_transcript = transcribe_audio(audio_path)
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(audio_transcript, f, indent=2, ensure_ascii=False)
            print("Audio transcription successful.")
        except Exception as e:
            print(f"Warning: Audio transcription anchor failed or skipped: {e}")
            
        # Step 2: Upload video to Gemini Files API
        print("\n--- STEP 2: Uploading Video to Gemini Files API ---")
        file_name, file_uri = client.upload_file(video_path)
        if not client.wait_for_file_active(file_name):
            raise RuntimeError("Video processing timed out on Gemini Files API.")
            
        # Step 2: Generate Ad Script and Scene Cues
        print("\n--- STEP 2: Generating Script and Scene Cues ---")
        ad_script = generate_script(client, video_path, file_uri, audio_transcript)
        with open(script_output_path, "w", encoding="utf-8") as f:
            json.dump(ad_script, f, indent=2, ensure_ascii=False)
        print(f"Ad script written to {script_output_path}")
        
        # Step 3: Market price lookup
        print("\n--- STEP 3: Market Price Grounding ---")
        market_query = ad_script.get("overall_market_query", "Range Rover Sport 2021 price India")
        market_report = lookup_market_price(client, market_query)
        market_path = os.path.join(output_dir, "market_price_report.txt")
        with open(market_path, "w", encoding="utf-8") as f:
            f.write(market_report)
        print(f"Market price report saved to {market_path}")
    


    # Step 4: Generate Voiceover using Gemini TTS
    print("\n--- STEP 4: Generating Voiceover ---")
    scene_cues = ad_script.get("scene_cues", [])
    tts_audios = generate_voiceover(client, scene_cues, temp_dir)
    
    # Step 5/6: Align and generate Subtitles
    print("\n--- STEP 5/6: Aligning Subtitles & Styling ---")
    aligned_scenes = []
    for i, scene in enumerate(scene_cues):
        tts_audio = tts_audios[i]
        hinglish_text = scene.get("ad_copy_hinglish", "")
        if tts_audio and os.path.exists(tts_audio):
            word_alignments = align_subtitles(tts_audio, hinglish_text)
            aligned_scenes.append({
                "scene_start": scene["start_time"],
                "words": word_alignments
            })
            
    subtitle_ass_path = os.path.join(output_dir, "subtitles.ass")
    generate_ass_subtitles(aligned_scenes, ad_script.get("car_details", []), subtitle_ass_path)
    
    # Step 7/8/9: Compile and render final video
    print("\n--- STEP 7/8/9: Video Compilation and Rendering ---")
    # Choose music
    bg_music = get_upbeat_bgm(output_dir)
    final_video_path = os.path.join(output_dir, "final_ad_video.mp4")
    compile_ad(video_path, scene_cues, tts_audios, bg_music, subtitle_ass_path, temp_dir, final_video_path)
    
    duration = time.time() - start_time
    print(f"\nPipeline finished successfully in {duration/60:.2f} minutes!")
    print(f"Final output: {final_video_path}")

def get_upbeat_bgm(output_dir: str) -> str:
    import requests
    import subprocess
    
    cache_dir = "/root/yt-auto/cache_music"
    os.makedirs(cache_dir, exist_ok=True)
    
    # Try fetching upbeat track using FREESOUND_API_KEY if available in local_env.sh
    freesound_key = ""
    local_env = "/root/yt-auto/local_env.sh"
    if os.path.exists(local_env):
        with open(local_env, "r") as f:
            for line in f:
                if "export FREESOUND_API_KEY=" in line:
                    freesound_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
                    
    if freesound_key:
        queries = ["upbeat corporate music", "happy background music", "positive corporate instrumental"]
        search_url = "https://freesound.org/apiv2/search/text/"
        for q in queries:
            print(f"[Music Search] Querying Freesound for: {q}")
            params = {
                "query": q,
                "filter": "duration:[30 TO 120]",
                "fields": "id,name,duration,previews",
                "page_size": 3,
                "token": freesound_key,
            }
            try:
                r = requests.get(search_url, params=params, timeout=15)
                r.raise_for_status()
                results = r.json().get("results", [])
                if results:
                    pick = results[0]
                    sound_id = pick["id"]
                    preview_url = pick["previews"]["preview-hq-mp3"]
                    cache_path = os.path.join(cache_dir, f"freesound_{sound_id}.wav")
                    
                    if os.path.exists(cache_path):
                        print(f"[Music Search] Found cached upbeat BGM: {cache_path}")
                        return cache_path
                        
                    print(f"[Music Search] Downloading upbeat track: {pick['name']}")
                    mp3_path = os.path.join(output_dir, "temp_bgm.mp3")
                    dl = requests.get(preview_url, timeout=20)
                    dl.raise_for_status()
                    with open(mp3_path, "wb") as f:
                        f.write(dl.content)
                    
                    # Convert to mono WAV
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", mp3_path, "-ar", "44100", "-ac", "1", cache_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                    )
                    if os.path.exists(mp3_path):
                        os.remove(mp3_path)
                    return cache_path
            except Exception as e:
                print(f"[Music Search] Search failed for {q}: {e}")
                
    # Fallback to known energetic cached tracks
    fallbacks = [
        "freesound_592783.wav",
        "freesound_785704.wav",
        "freesound_620197.wav"
    ]
    for fb in fallbacks:
        fb_path = os.path.join(cache_dir, fb)
        if os.path.exists(fb_path):
            print(f"[Music Search] Using fallback cached upbeat BGM: {fb_path}")
            return fb_path
            
    # Absolute fallback
    default_bg = "/root/yt-auto/cache_music/freesound_432835.wav"
    print(f"[Music Search] Using default BGM: {default_bg}")
    return default_bg

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pipeline.py <video_path> <output_dir> [--resume]")
        sys.exit(1)
    resume = "--resume" in sys.argv
    run_pipeline(sys.argv[1], sys.argv[2], resume=resume)

