import os
import json
import subprocess
from car_ad_pipeline.gemini_client import GeminiClient

SCHEMA = {
    "type": "object",
    "properties": {
        "car_details": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "color": {"type": "string"},
                    "specs": {"type": "string"},
                    "visual_timestamps": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "number"},
                            "end": {"type": "number"}
                        },
                        "required": ["start", "end"]
                    },
                    "odometer_value": {"type": "string"},
                    "odometer_confidence": {"type": "string", "enum": ["high", "low"]},
                    "price_value": {"type": "string"},
                    "price_confidence": {"type": "string", "enum": ["high", "low"]}
                },
                "required": ["model", "color", "specs", "visual_timestamps", "odometer_confidence", "price_confidence"]
            }
        },
        "scene_cues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "number"},
                    "end_time": {"type": "number"},
                    "visual_description": {"type": "string"},
                    "detected_text_overlays": {"type": "string"},
                    "ad_copy_hindi": {"type": "string"},
                    "ad_copy_hinglish": {"type": "string"},
                    "visual_focus": {"type": "string"}
                },
                "required": ["start_time", "end_time", "visual_description", "ad_copy_hindi", "ad_copy_hinglish", "visual_focus"]
            }
        },
        "overall_market_query": {"type": "string"}
    },
    "required": ["car_details", "scene_cues", "overall_market_query"]
}

def extract_clip(video_path: str, start: float, end: float, output_path: str):
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c", "copy",
        output_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def generate_script(client: GeminiClient, video_path: str, file_uri: str, audio_transcript: dict) -> dict:
    transcript_str = json.dumps(audio_transcript, indent=2, ensure_ascii=False) if audio_transcript else "No audio transcription available."
    
    # Prompt splitting into neutral feature extraction read and copywriting write
    prompt = f"""
ROLE: You are an elite automotive visual analyst and a premium car ad copywriter.

=== STEP 2c: AUDIO TIMING ANCHOR ===
The following is the rough word-level audio transcript from the presenter in the video:
{transcript_str}
Use this transcript as a timing anchor. When describing scenes and writing ad copy, ensure that your visual descriptions at specific timestamps match the spoken claims in the transcript (e.g. if he says "Defender" at 0:06, the visual model and timestamps must align).

=== STEP 2: NEUTRAL READ (FEATURE EXTRACTION) ===
1. Watch the video file and extract all literal features of the vehicles shown.
2. List the car model, year, variant, color, fuel type, transmission, odometer readings, owner details, and asking price from the text overlays and the presenter's speech.
3. Be strictly factual. Do NOT assume any features (e.g., sunroof, ventilated seats) unless they are clearly visible or spoken.
4. For odometer readings and price cards, evaluate if they are clearly legible in the video. If they are blurry, small, or hard to read, set the corresponding confidence flag to "low". Otherwise set to "high".

=== STEP 2: PERSUASIVE WRITE (COPYWRITING SALESMAN COPY) ===
1. Using the extracted features, write a premium, conversational, and highly hooky salesman-style ad copy.
2. The tone must be confident and friendly, NOT pushy or desperate.
3. The hook must land in the first 3 seconds (e.g., "Flood of Range Rovers at Modern Car Point!").
4. Use feature -> benefit framing (e.g., "Diesel HSE variant - which means brute power combined with unmatched fuel efficiency on long highways!").
5. Do NOT round numbers. Speak precise numbers like 87,000 km or ₹ 45 Lakhs to build high client trust.
6. Generate two versions of the ad copy for each scene:
   - `ad_copy_hindi`: Spoken narration copy in Hindi (Devanagari script) containing emotional inline direction tags like [excited], [confident], [pause].
   - `ad_copy_hinglish`: Subtitle text in Hinglish (Hindi spoken words written transliterated using English/Latin alphabet, e.g. "sabse pehli gaadi" instead of "first car is"). It MUST match the spoken Hindi words exactly but written in English characters, NOT English translations.

Generate the output strictly conforming to the specified JSON schema.
"""

    contents = [
        {
            "role": "user",
            "parts": [
                {"fileData": {"mimeType": "video/mp4", "fileUri": file_uri}},
                {"text": prompt}
            ]
        }
    ]
    
    # Run vision pass with media_resolution: low
    print("Running vision pass (low-res)...")
    result_text = client.generate_content(contents, response_schema=SCHEMA, low_res=True)
    result = json.loads(result_text)
    
    # STEP 2b: Targeted re-read pass
    for i, car in enumerate(result.get("car_details", [])):
        re_read_triggered = False
        re_read_query = ""
        
        if car.get("odometer_confidence") == "low":
            re_read_triggered = True
            re_read_query = "What exact odometer or kilometer reading is visible on the car's screen or instrument cluster?"
        if car.get("price_confidence") == "low":
            re_read_triggered = True
            re_read_query += " What exact price or numeric demand is shown on the screen or info cards?"
            
        if re_read_triggered:
            start = max(0.0, car["visual_timestamps"]["start"])
            end = car["visual_timestamps"]["end"]
            print(f"[Targeted Re-Read] Low confidence flagged for {car['model']}. Clipping {start}s to {end}s...")
            
            clip_path = f"/root/scratch/temp_clip_{i}.mp4"
            extract_clip(video_path, start, end, clip_path)
            
            try:
                # Upload small clip and query at default/high media resolution
                clip_name, clip_uri = client.upload_file(clip_path)
                if client.wait_for_file_active(clip_name):
                    print(f"Querying clip at DEFAULT resolution: {re_read_query}")
                    clip_prompt = f"Analyze this short video clip. {re_read_query} Give a direct answer with precise numbers."
                    clip_contents = [
                        {
                            "role": "user",
                            "parts": [
                                {"fileData": {"mimeType": "video/mp4", "fileUri": clip_uri}},
                                {"text": clip_prompt}
                            ]
                        }
                    ]
                    # Default media resolution call
                    clarification = client.generate_content(clip_contents, low_res=False)
                    print(f"[Targeted Re-Read Result] {clarification}")
                    
                    # Update the structured results with the clarified details
                    # Send a structured correction pass to incorporate the new reading
                    correction_prompt = f"""
Update the following JSON data with the clarified information.
Clarification: {clarification}
Original JSON: {json.dumps(result)}
Return the updated JSON conforming to the original schema.
"""
                    updated_json_str = client.generate_content(
                        [
                            {
                                "role": "user",
                                "parts": [{"text": correction_prompt}]
                            }
                        ],
                        response_schema=SCHEMA
                    )
                    result = json.loads(updated_json_str)
            except Exception as ex:
                print(f"Targeted re-read failed: {ex}")
            finally:
                if os.path.exists(clip_path):
                    os.remove(clip_path)
                    
    return result
