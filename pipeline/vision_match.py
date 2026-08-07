import base64
import io
import json
from PIL import Image
from pipeline.gemini import _post_with_rotation
from pipeline.config import GEMINI_FLASH, GEMINI_API_BASE

def _shrink(img_bytes: bytes, max_dim: int = 768) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()

def vision_rank_broll(
    thumbnails: list[bytes],
    narration: str,
    query: str,
) -> tuple[int | None, bool]:
    """
    Scores candidate B-roll thumbnails against the EXACT narration sentence.
    Ranks candidates by semantic fit, not first-provider wins.
    Returns (best_index, match_found).
    match_found=True means the best available candidate is worth using;
    final Judge AI can still reject/repair the assembled segment later.
    """
    if not thumbnails:
        return None, False

    import os
    if os.environ.get("BYPASS_VISION_MATCH") == "1":
        print("[VisionMatch] Bypassing Vision Match (BYPASS_VISION_MATCH=1). Accepting index 0.")
        return 0, True

    # Build the strict matching prompt
    prompt_text = (
        f"NARRATION (exact sentence for this video segment):\n"
        f"\"{narration}\"\n\n"
        f"SEARCH QUERY used: \"{query}\"\n\n"
        f"You are evaluating {len(thumbnails)} candidate B-roll image(s) (indexed 0 to {len(thumbnails) - 1}) for the above narration.\n"
        f"Note: Some candidate images may be a horizontal collage showing 3 sequential frames from the same video. Use this sequence to understand the video motion and content.\n\n"
        f"SCORING RULES — read carefully:\n"
        f"1. CRITICAL ZERO-SCORE REJECTION (SCORE = 0 IMMEDIATELY):\n"
        f"   - ANY candidate showing full-screen text, title cards, subtitles, lower-third graphics, channel logos, or text-only slides from the source video.\n"
        f"   - ANY candidate showing static PowerPoint slides or text banners.\n"
        f"   - ANY candidate showing black screens, dark loading screens, transition flashes, or fade-outs.\n"
        f"2. High-quality documentary clips, real-world science demonstrations, expert presenters, historical archive footage, and dynamic physical visuals MUST be accepted (scores 75-95).\n"
        f"3. Score every candidate from 0-100:\n"
        f"   - 90-100: exact physical subject or highly specific real-world match (clean active video footage, documentary clip, or 3D science render)\n"
        f"   - 75-89: strong contextual/thematic physical or documentary match of the main subject\n"
        f"   - 50-74: usable fallback active visual related to the topic\n"
        f"   - 0-49: bad mismatch, static text slide, title card, black screen, or completely unrelated topic\n"
        f"4. REJECT (SCORE = 0):\n"
        f"   - Full-screen text, title cards, black screens, or promotional app ads\n"
        f"   - Static PowerPoint slides or text banners\n"
        f"5. Pick the highest-scoring candidate even when imperfect, so the pipeline can use the best available real video asset.\n"
        f"6. Set match_found=false only when the best candidate scores below 50.\n\n"
        f"Return ONLY valid JSON (no markdown):\n"
        f'{{"best_index": <int or null>, '
        f'"match_found": <bool>, '
        f'"confidence": <0-100 int>, '
        f'"candidate_scores": [<0-100 int for each candidate>], '
        f'"reject_reason": \"<why rejected, or empty string if accepted>\"}}\n\n'
        f"Set match_found=true if confidence >= 50. Still explain weaknesses in reject_reason if confidence < 70."
    )

    parts = [{"text": prompt_text}]

    for t in thumbnails:
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": base64.b64encode(_shrink(t)).decode(),
            }
        })

    url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.05,   # very low — deterministic judgment
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = _post_with_rotation(url, payload, timeout=60)
        raw  = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(raw)

        idx        = data.get("best_index")
        found      = bool(data.get("match_found", False))
        confidence = int(data.get("confidence", 0))
        scores     = data.get("candidate_scores", [])
        reason     = data.get("reject_reason", "")

        if isinstance(scores, list) and scores:
            print(f"[VisionMatch] Candidate scores: {scores}")
        if reason:
            print(f"[VisionMatch] Note: {reason} (confidence={confidence})")

        # Find highest scoring candidate with score >= 50 from candidate_scores list
        best_candidate_idx = None
        highest_score = 0
        if isinstance(scores, list) and len(scores) == len(thumbnails):
            for s_idx, score in enumerate(scores):
                if isinstance(score, (int, float)) and score >= 50 and score > highest_score:
                    highest_score = score
                    best_candidate_idx = s_idx

        if best_candidate_idx is not None:
            quality = "strong" if highest_score >= 70 else "usable"
            print(f"[VisionMatch] Accepted {quality} index {best_candidate_idx} (score={highest_score})")
            return best_candidate_idx, True

        # Check model's best_index if confidence is valid
        if found and isinstance(idx, int) and 0 <= idx < len(thumbnails) and confidence >= 50:
            quality = "strong" if confidence >= 70 else "usable"
            print(f"[VisionMatch] Accepted {quality} index {idx} (confidence={confidence})")
            return idx, True

        # ABSOLUTE REJECTION: Do NOT force Candidate 0 if all candidates score < 50
        print(f"[VisionMatch] All candidate scores below 50 (scores={scores}). Strictly rejecting batch.")
        return None, False

    except Exception as e:
        print(f"[VisionMatch] API error/rate-limited ({e}). Fallback accepting top candidate 0 to prevent static image fallback.")
        return 0, True
