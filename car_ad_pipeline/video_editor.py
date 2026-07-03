import os
import subprocess
import json

def get_audio_duration(audio_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]
    try:
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception as e:
        print(f"Error getting audio duration for {audio_path}: {e}")
        return 5.0

def build_scene_clip(raw_video: str, start: float, end: float, tts_audio: str, output_path: str):
    vdur = end - start
    adur = get_audio_duration(tts_audio)
    
    print(f"Processing scene clip: video={vdur:.2f}s, tts={adur:.2f}s")
    
    temp_v = output_path.replace(".mp4", "_v.mp4")
    
    if vdur >= adur:
        # Cut video to match audio length
        cmd_v = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{start + adur:.3f}",
            "-i", raw_video,
            "-an",
            "-c:v", "libx264",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            temp_v
        ]
        result = subprocess.run(cmd_v, capture_output=True)
        if result.returncode != 0:
            print(f"FFmpeg scene video cut stderr: {result.stderr.decode()[-500:]}")
            raise RuntimeError(f"FFmpeg scene video cut failed with exit code {result.returncode}")
    else:
        # Cut the clip of exact length vdur, then loop it up to adur
        temp_cut = output_path.replace(".mp4", "_cut.mp4")
        cmd_cut = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", raw_video,
            "-an",
            "-c:v", "libx264",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            temp_cut
        ]
        result = subprocess.run(cmd_cut, capture_output=True)
        if result.returncode != 0:
            print(f"FFmpeg cut clip stderr: {result.stderr.decode()[-500:]}")
            raise RuntimeError(f"FFmpeg cut clip failed with exit code {result.returncode}")
            
        # Loop the cut clip up to adur
        cmd_loop = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", temp_cut,
            "-t", f"{adur:.3f}",
            "-c:v", "libx264",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            temp_v
        ]
        result = subprocess.run(cmd_loop, capture_output=True)
        if os.path.exists(temp_cut):
            os.remove(temp_cut)
        if result.returncode != 0:
            print(f"FFmpeg loop video stderr: {result.stderr.decode()[-500:]}")
            raise RuntimeError(f"FFmpeg loop video failed with exit code {result.returncode}")
    
    # Merge video and TTS audio
    cmd_merge = [
        "ffmpeg", "-y",
        "-i", temp_v,
        "-i", tts_audio,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v",
        "-map", "1:a",
        output_path
    ]
    result = subprocess.run(cmd_merge, capture_output=True)
    if result.returncode != 0:
        print(f"FFmpeg merge stderr: {result.stderr.decode()[-500:]}")
        raise RuntimeError(f"FFmpeg merge failed with exit code {result.returncode}")
    
    if os.path.exists(temp_v):
        os.remove(temp_v)

def compile_ad(raw_video: str, scene_cues: list, tts_audios: list, bg_music: str, subtitle_ass: str, output_dir: str, final_path: str):
    print("Compiling final ad video...")
    os.makedirs(output_dir, exist_ok=True)
    
    scene_clips = []
    
    for i, scene in enumerate(scene_cues):
        start = scene["start_time"]
        end = scene["end_time"]
        tts_audio = tts_audios[i]
        
        if not tts_audio or not os.path.exists(tts_audio):
            continue
            
        scene_clip_path = os.path.join(output_dir, f"scene_{i+1}_final.mp4")
        build_scene_clip(raw_video, start, end, tts_audio, scene_clip_path)
        scene_clips.append(scene_clip_path)
        
    # Write concat list
    concat_list_path = os.path.join(output_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for clip in scene_clips:
            f.write(f"file '{clip}'\n")
            
    # Concatenate clips
    concatenated_raw = os.path.join(output_dir, "concatenated_raw.mp4")
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        concatenated_raw
    ]
    result = subprocess.run(cmd_concat, check=True, capture_output=True)
    if result.returncode != 0:
        print(f"FFmpeg concat stderr: {result.stderr.decode()}")
    
    # Mix background music, SFX track, and burn ASS subtitles with LUT/contrast filter
    print("Generating SFX and mixing final audio/video...")
    
    # Calculate boundary times for SFX whooshes
    durations = []
    for a in tts_audios:
        if a and os.path.exists(a):
            durations.append(get_audio_duration(a))
        else:
            durations.append(5.0)
            
    boundary_times = []
    curr = 0.0
    for d in durations[:-1]:
        curr += d
        boundary_times.append(curr)
    total_dur = curr + durations[-1]
    
    # Generate SFX track
    sfx_track_path = None
    try:
        from pipeline.sfx import create_sfx_track
        print(f"Creating SFX track for boundary times: {boundary_times} (total duration: {total_dur:.2f}s)")
        sfx_temp = create_sfx_track(boundary_times, total_dur, sample_rate=44100, whoosh_volume=0.35, topic="car ad")
        if sfx_temp and os.path.exists(sfx_temp):
            sfx_track_path = os.path.join(output_dir, "sfx_track.wav")
            import shutil
            shutil.copy(sfx_temp, sfx_track_path)
            print(f"SFX track copied to {sfx_track_path}")
    except Exception as e:
        print(f"Warning: Failed to create SFX track: {e}")
        
    vf_filter = f"ass={subtitle_ass},eq=contrast=1.08:saturation=1.18:brightness=0.01"
    
    if sfx_track_path and os.path.exists(sfx_track_path):
        print("Mixing Main Audio + BGM + SFX...")
        cmd_final = [
            "ffmpeg", "-y",
            "-i", concatenated_raw,
            "-i", bg_music,
            "-i", sfx_track_path,
            "-filter_complex", f"[0:a]volume=1.0[v_a];[1:a]volume=0.20[m_a];[2:a]volume=0.45[s_a];[v_a][m_a][s_a]amix=inputs=3:duration=first:normalize=0[a];[0:v]{vf_filter}[v]",
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-crf", "21",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            final_path
        ]
    else:
        print("Mixing Main Audio + BGM only (no SFX)...")
        cmd_final = [
            "ffmpeg", "-y",
            "-i", concatenated_raw,
            "-i", bg_music,
            "-filter_complex", f"[0:a]volume=1.0[v_a];[1:a]volume=0.20[m_a];[v_a][m_a]amix=inputs=2:duration=first:normalize=0[a];[0:v]{vf_filter}[v]",
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-crf", "21",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            final_path
        ]
        
    result = subprocess.run(cmd_final, capture_output=True)
    if result.returncode != 0:
        print(f"FFmpeg final stderr: {result.stderr.decode()[-500:]}")
        raise RuntimeError(f"FFmpeg final render failed with exit code {result.returncode}")
    
    if not os.path.exists(final_path):
        raise RuntimeError(f"Final video not found at {final_path} after ffmpeg!")
    print(f"Final video successfully generated at {final_path}!")
