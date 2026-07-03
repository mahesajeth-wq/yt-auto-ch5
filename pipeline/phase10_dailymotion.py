import os
import sys
import json
import dailymotion

def upload_to_dailymotion(video_path: str, metadata: dict) -> str:
    # ── 1. Resolve credentials ────────────────────────────────────────────────
    api_key = os.environ.get("DM_API_KEY", "").strip()
    api_secret = os.environ.get("DM_API_SECRET", "").strip()
    username = os.environ.get("DM_USERNAME", "").strip()
    password = os.environ.get("DM_PASSWORD", "").strip()
    category = "school"

    if not api_key or not api_secret or not username or not password:
        print("[DM] Warning: Missing Dailymotion environment credentials. Skipping upload.")
        return ""

    # ── 2. Authenticate ───────────────────────────────────────────────────────
    print(f"[DM] Initializing Dailymotion client for: {username}")
    
    d = dailymotion.Dailymotion()
    d.set_grant_type(
        'password',
        api_key=api_key,
        api_secret=api_secret,
        scope=['manage_videos'],
        info={
            'username': username,
            'password': password,
        }
    )

    # ── 3. Prepare Metadata ───────────────────────────────────────────────────
    title = metadata.get("title", "Educational Video")[:100].replace('<', '').replace('>', '')
    description = metadata.get("description", "The mechanism. The fix. The lesson.")[:5000].replace('<', '').replace('>', '')
    
    tags_list = metadata.get("tags", [])
    if isinstance(tags_list, str):
        tags_str = tags_list
    else:
        tags_str = ",".join(str(t)[:100].replace('<', '').replace('>', '') for t in tags_list)[:500]

    # ── 4. Upload & Publish ───────────────────────────────────────────────────
    print(f"[DM] Uploading video file: {video_path}...")
    hosted_url = d.upload(video_path)
    print(f"[DM] Video hosted successfully at: {hosted_url}")

    print(f"[DM] Publishing video: '{title}' under category '{category}'...")
    response = d.post(
        "/me/videos",
        {
            "url": hosted_url,
            "title": title,
            "description": description,
            "tags": tags_str,
            "channel": category,
            "published": "true",
            "private": "false",
            "is_created_for_kids": "false",
        }
    )

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"[DM] Publish failed. Full response: {response}")

    print(f"[DM] Published successfully! Video URL: https://www.dailymotion.com/video/{video_id}")
    return video_id
