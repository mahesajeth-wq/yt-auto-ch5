import os
import wave
import shutil
import subprocess
import json
import re
from pipeline.sfx import create_sfx_track

def get_wav_duration(filepath: str) -> float:
    with wave.open(filepath, 'rb') as f:
        frames = f.getnframes()
        rate = f.getframerate()
        return frames / float(rate)

def get_video_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filepath
    ]
    try:
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        return 0.0

def assemble_video(broll_files: list[str], tts_files: list[str], captions_ass: str, music_path: str, script: dict, format_type: str) -> str:
    print("Starting video assembly...")
    os.makedirs("output", exist_ok=True)
    
    # Step 1: Normalize all B-roll clips to uniform spec
    print("Step 1: Normalizing B-roll clips...")
    normalized_brolls = []
    durations = []
    ss_offsets = []
    
    w, h = (1080, 1920) if format_type == "short" else (1920, 1080)
    
    footage_credits = []
    seen_handles = set()

    for i, (broll_path, tts_path) in enumerate(zip(broll_files, tts_files)):
        duration = get_wav_duration(tts_path)
        durations.append(duration)
        norm_path = f"output/broll_{i}_norm.mp4"
        
        # Calculate dynamic start offset to skip black screen / intro slides in long videos
        total_dur = get_video_duration(broll_path)
        ss_offset = 0.0
        if total_dur > 30.0:
            # Skip first 20%, up to 30s
            ss_offset = min(30.0, total_dur * 0.2)
        elif total_dur > 15.0:
            # Skip first 3 seconds
            ss_offset = 3.0
        elif total_dur > 8.0:
            ss_offset = 1.0
            
        if ss_offset + duration > total_dur:
            ss_offset = max(0.0, total_dur - duration)
        ss_offsets.append(ss_offset)

    for i, (broll_path, tts_path, seg) in enumerate(zip(broll_files, tts_files, script["segments"])):
        duration = durations[i]
        ss_offset = ss_offsets[i]
        norm_path = f"output/broll_{i}_norm.mp4"
        
        # Handle missing/None broll_path by generating a unique 4K Pollinations AI motion clip for this specific segment
        if not broll_path or not os.path.exists(broll_path) or os.path.getsize(broll_path) < 10_000:
            broll_path = f"output/emergency_broll_{i}.mp4"
            print(f"[Assemble] Missing/invalid B-roll for segment {i}. Generating unique 4K Pollinations AI motion clip...")
            seg_info = script.get("segments", [])[i] if script and i < len(script.get("segments", [])) else {}
            seg_query = seg_info.get("broll_query") or seg_info.get("narration") or "cinematic nature background 4k"
            prompt_clean = f"4k cinematic documentary footage of {seg_query}, photorealistic, 8k, detailed, no text, no watermark"
            from pipeline.phase4_broll import _pollinations_image, _image_to_ken_burns_video
            synth_img = f"output/emergency_img_{i}.jpg"
            if _pollinations_image(prompt_clean, synth_img, w=2160, h=3840):
                _image_to_ken_burns_video(synth_img, broll_path, w, h, duration=duration)
            else:
                print(f"[Assemble] Warning: Pollinations AI failed for segment {i}. Downloading 4K Unsplash photorealistic stock background...")
                import urllib.request
                fallback_img_url = f"https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=2160&q=80"
                try:
                    urllib.request.urlretrieve(fallback_img_url, synth_img)
                    _image_to_ken_burns_video(synth_img, broll_path, w, h, duration=duration)
                except Exception:
                    cmd_synth = [
                        "ffmpeg", "-y", "-f", "lavfi",
                        "-i", f"cellauto=s={w}x{h}:d={duration}:rule=30",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", broll_path
                    ]
                    subprocess.run(cmd_synth, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"Normalizing segment {i} B-roll to duration {duration:.3f}s (offset: {ss_offset:.3f}s)...")

        drawtext_chain = ""
        credit_file = f"output/broll_{i}_credit.json"
        if os.path.exists(credit_file):
            try:
                with open(credit_file, "r") as cf:
                    cdata = json.load(cf)
                    handle = cdata.get("uploader_handle") or "@YouTube"
                    title = cdata.get("title") or ""
                    url = cdata.get("video_url") or ""
                    if handle:
                        if handle not in seen_handles:
                            seen_handles.add(handle)
                            footage_credits.append({
                                "handle": handle,
                                "url": url,
                                "title": title
                            })
                        clean_handle = re.sub(r"[^a-zA-Z0-9_@-]", "", str(handle))
                        clean_txt = f"Footage\\: {clean_handle}"
                        drawtext_chain = f",drawtext=text='{clean_txt}':x=40:y=80:fontsize=22:fontcolor=white:shadowcolor=black@0.8:shadowx=2:shadowy=2:enable='between(t,0,3.5)'"
                        print(f"[Assemble] Burning clean on-screen attribution badge for segment {i}: {handle}")
            except Exception as cerr:
                print(f"[Assemble] Warning: Could not parse credit file {credit_file}: {cerr}")

        # Select randomized cinematic camera motion (Ken Burns / Pan / Zoom)
        import random as _rnd
        motion_idx = _rnd.randint(0, 4)
        
        # Base scale-crop to cover full bleed with unsharp masking for enhanced clarity
        if motion_idx == 0:
            # 1. Slow Cinematic Diagonal Pan Up-Right
            vf_chain = (
                f"scale=trunc({w}*1.15/2)*2:trunc({h}*1.15/2)*2:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:'(in_w-out_w)/2 + (t-{duration}/2)*15':'(in_h-out_h)/2 + (t-{duration}/2)*15',"
                f"eq=contrast=1.06:saturation=1.12:gamma=0.96,unsharp=5:5:0.8:5:5:0.4,vignette=angle=0.4,setsar=1" + drawtext_chain
            )
        elif motion_idx == 1:
            # 2. Slow Panning Upward
            vf_chain = (
                f"scale=trunc({w}*1.15/2)*2:trunc({h}*1.15/2)*2:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:'(in_w-out_w)/2':'(in_h-out_h)/2 + (t-{duration}/2)*22',"
                f"eq=contrast=1.06:saturation=1.12:gamma=0.96,unsharp=5:5:0.8:5:5:0.4,vignette=angle=0.4,setsar=1" + drawtext_chain
            )
        elif motion_idx == 2:
            # 3. Slow Panning Downward
            vf_chain = (
                f"scale=trunc({w}*1.15/2)*2:trunc({h}*1.15/2)*2:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:'(in_w-out_w)/2':'(in_h-out_h)/2 - (t-{duration}/2)*22',"
                f"eq=contrast=1.06:saturation=1.12:gamma=0.96,unsharp=5:5:0.8:5:5:0.4,vignette=angle=0.4,setsar=1" + drawtext_chain
            )
        elif motion_idx == 3:
            # 4. Slow Panning Right
            vf_chain = (
                f"scale=trunc({w}*1.15/2)*2:trunc({h}*1.15/2)*2:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:'(in_w-out_w)/2 + (t-{duration}/2)*22':'(in_h-out_h)/2',"
                f"eq=contrast=1.06:saturation=1.12:gamma=0.96,unsharp=5:5:0.8:5:5:0.4,vignette=angle=0.4,setsar=1" + drawtext_chain
            )
        else:
            # 5. Slow Panning Left
            vf_chain = (
                f"scale=trunc({w}*1.15/2)*2:trunc({h}*1.15/2)*2:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:'(in_w-out_w)/2 - (t-{duration}/2)*22':'(in_h-out_h)/2',"
                f"eq=contrast=1.06:saturation=1.12:gamma=0.96,unsharp=5:5:0.8:5:5:0.4,vignette=angle=0.4,setsar=1" + drawtext_chain
            )
            
        cmd = [
            "ffmpeg", "-y", "-ss", f"{ss_offset:.3f}", "-stream_loop", "-1", "-i", broll_path, "-t", f"{duration:.3f}",
            "-vf", vf_chain,
            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", norm_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        normalized_brolls.append(norm_path)

    if footage_credits:
        try:
            with open("output/footage_credits.json", "w") as fcf:
                json.dump(footage_credits, fcf, indent=2)
            print(f"[Assemble] Saved {len(footage_credits)} footage credit entries to output/footage_credits.json.")
        except Exception as fc_err:
            print(f"[Assemble] Warning: Could not save footage_credits.json: {fc_err}")

    # Step 2: Concatenate B-roll (no audio)
    print("Step 2: Concatenating B-roll clips...")
    concat_list_path = "output/concat_list.txt"
    with open(concat_list_path, "w") as f:
        for norm_path in normalized_brolls:
            abs_path = os.path.abspath(norm_path)
            f.write(f"file '{abs_path}'\n")
            
    assembled_video_path = "output/assembled_video.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        assembled_video_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 3: Concatenate TTS audio segments
    print("Step 3: Concatenating TTS audio segments...")
    audio_list_path = "output/audio_list.txt"
    with open(audio_list_path, "w") as f:
        for tts_path in tts_files:
            abs_path = os.path.abspath(tts_path)
            f.write(f"file '{abs_path}'\n")
            
    tts_combined_path = "output/tts_combined.wav"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", audio_list_path,
        "-c", "copy", tts_combined_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 3b: Create SFX track (whoosh at each clip boundary)
    print("Step 3b: Generating SFX track…")
    total_tts_duration = sum(durations)
    # Clip boundaries are at cumulative TTS durations (skip the first clip — no whoosh at t=0)
    boundary_times = []
    cumulative = 0.0
    for d in durations[:-1]:   # all boundaries except the last (end of video)
        cumulative += d
        boundary_times.append(cumulative)
    sfx_track_path = create_sfx_track(boundary_times, total_tts_duration, topic=script.get("topic", ""))

    # Step 4: Add karaoke captions to video
    print("Step 4: Adding captions...")
    assembled_capped_path = "output/assembled_capped.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", assembled_video_path,
        "-vf", f"ass={captions_ass}",
        "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-pix_fmt", "yuv420p",
        assembled_capped_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 5: Adding premium hook overlays and transitions
    print("Step 5: Adding premium hook overlays and transitions...")
    assembled_flashed_path = "output/assembled_flashed.mp4"
    if format_type == "short":
        clean_title = "".join(c for c in script.get("title", "").upper() if c.isalnum() or c.isspace()).strip()
        
        filters = []
        # 1. Pattern interrupt flashes at the start of each segment (0.15s transparent white/black/color overlay)
        overlay_colors = ["white@0.3", "black@0.45", "yellow@0.15", "orange@0.2"]
        for idx, t_start in enumerate([0.0] + boundary_times):
            color = overlay_colors[idx % len(overlay_colors)]
            filters.append(f"drawbox=y=0:color={color}:t=fill:enable='between(t,{t_start:.3f},{t_start+0.15:.3f})'")
            
        # Get font name as a separate variable to avoid backslash inside f-string expression (unsupported in Python 3.11)
        font_name = script.get('font_name', 'Bebas Neue')
            
        # 2. Big title hook card (first 1.5s) - Yellow font with premium box padding
        # Clamp title to max 28 chars per line to prevent overflow, reduce fontsize if very long
        words = clean_title.split()
        title_lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip() if current_line else word
            if len(test_line) <= 22:
                current_line = test_line
            else:
                if current_line:
                    title_lines.append(current_line)
                current_line = word
        if current_line:
            title_lines.append(current_line)
        
        # Use smaller font if title is multi-line
        title_fontsize = 64 if len(title_lines) <= 1 else 50
        y_start = 0.16
        for t_idx, line in enumerate(title_lines):
            clean_l = line.replace("'", "").replace(":", "\\:").strip()
            y_pos = f"h*{y_start + t_idx * 0.06:.2f}"
            filters.append(
                f"drawtext=text='{clean_l}':fontsize={title_fontsize}:fontcolor=yellow:font='{font_name}':"
                f"x=(w-text_w)/2:y={y_pos}:enable='between(t,0,1.8)':shadowcolor=black@0.9:shadowx=4:shadowy=4:borderw=4:bordercolor=black"
            )
                       
        if len(durations) >= 4:
            seg4_start = sum(durations[:3])
            seg4_end = seg4_start + 0.8
            # 3. Rewatch trigger positioned cleanly at lower third
            filters.append(
                f"drawtext=text='PAUSE - CATCH THE DETAIL':fontsize=42:fontcolor=yellow:font='{font_name}':"
                f"x=(w-text_w)/2:y=h*0.82:enable='between(t,{seg4_start:.3f},{seg4_end:.3f})':"
                f"shadowcolor=black@0.9:shadowx=3:shadowy=3:borderw=3:bordercolor=black"
            )
            
        cmd = [
            "ffmpeg", "-y", "-i", assembled_capped_path,
            "-vf", ",".join(filters),
            "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-pix_fmt", "yuv420p",
            assembled_flashed_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # For long form, apply subtle black dip transitions at boundaries (0.25s)
        filters = []
        for t_start in boundary_times:
            filters.append(f"drawbox=y=0:color=black@0.7:t=fill:enable='between(t,{t_start-0.125:.3f},{t_start+0.125:.3f})'")
        
        if filters:
            cmd = [
                "ffmpeg", "-y", "-i", assembled_capped_path,
                "-vf", ",".join(filters),
                "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-pix_fmt", "yuv420p",
                assembled_flashed_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.copy(assembled_capped_path, assembled_flashed_path)

    # Step 6: Final mix: video + TTS + music + SFX
    print("Step 6: Final audio mix with SFX…")
    final_output_path = f"output/final_{format_type}.mp4"

    filter_complex = (
        "[1:a]volume=2.0,asplit=2[tts1][tts2];"
        # We increase baseline music volume since it will be auto-ducked during speech
        "[2:a]volume=0.25,aloop=loop=-1:size=2147483647[music_loop];"
        "[3:a]volume=0.35[sfx];"
        # sidechaincompress: ducks the music loop [music_loop] using the voiceover [tts1] as trigger
        "[music_loop][tts1]sidechaincompress=threshold=0.15:ratio=4:attack=50:release=300[music_ducked];"
        "[tts2][music_ducked]amix=inputs=2:duration=first:normalize=0[mixed];"
        "[mixed][sfx]amix=inputs=2:duration=first:normalize=0[premix];"
        "[premix]loudnorm=I=-14:TP=-1.5:LRA=11[audio_final]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", assembled_flashed_path,
        "-i", tts_combined_path,
        "-i", music_path,
        "-i", sfx_track_path,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[audio_final]",
        "-c:v", "copy",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-r", "30", "-movflags", "+faststart",
        final_output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"Assembly completed. Final video: {final_output_path}")
    return final_output_path
