import os

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"

def get_api_keys() -> list[str]:
    keys = []
    multi = os.environ.get("GEMINI_API_KEYS", "").strip()
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    single = os.environ.get("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    if not keys:
        # Fallback to local_env.sh values if running locally
        local_env = "/root/yt-auto/local_env.sh"
        if os.path.exists(local_env):
            with open(local_env, "r") as f:
                for line in f:
                    if "export GEMINI_API_KEYS=" in line:
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        keys.extend(k.strip() for k in val.split(",") if k.strip())
                    elif "export GEMINI_API_KEY=" in line and not keys:
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        keys.append(val)
    # Remove duplicates
    return list(dict.fromkeys(keys))
