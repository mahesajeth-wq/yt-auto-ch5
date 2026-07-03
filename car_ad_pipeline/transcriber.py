import os
import subprocess
import json

def extract_audio(video_path: str, audio_path: str):
    print(f"Extracting audio from {video_path} to {audio_path}...")
    if os.path.exists(audio_path):
        os.remove(audio_path)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def transcribe_audio(audio_path: str) -> dict:
    print("Bypassing Whisper model loading to save memory...")
    return {"text": "", "segments": [], "words": []}
    
    words_list = []
    text_segments = []
    
    for segment in segments:
        text_segments.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip()
        })
        if segment.words:
            for word in segment.words:
                words_list.append({
                    "word": word.word.strip(),
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability
                })
                
    return {
        "text": "".join([s["text"] for s in text_segments]),
        "segments": text_segments,
        "words": words_list
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python transcriber.py <video_path> <output_json>")
        sys.exit(1)
        
    video = sys.argv[1]
    out_json = sys.argv[2]
    audio = video.replace(".mp4", ".wav")
    
    extract_audio(video, audio)
    result = transcribe_audio(audio)
    
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Transcript written to {out_json}")
