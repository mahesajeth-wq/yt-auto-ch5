import os
import re
import random
import requests
import urllib.parse
import subprocess
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pipeline.config import PEXELS_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY, NASA_API_KEY, KLIPY_API_KEY, NASA_BROLL_ENABLED, GEMINI_API_BASE, GEMINI_FLASH



def _nasa_params(query: str, media_type: str, page_size: int) -> dict:
    return {
        "q": query,
        "media_type": media_type,
        "page_size": page_size,
        "keywords": query,
        "year_start": "2010"
    }


def _walk_urls(obj) -> list[str]:
    urls: list[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            urls.extend(_walk_urls(value))
    elif isinstance(obj, list):
        for value in obj:
            urls.extend(_walk_urls(value))
    elif isinstance(obj, str) and obj.startswith("http"):
        urls.append(obj)
    return urls


def _pick_klipy_urls(item: dict) -> tuple[str | None, str | None]:
    urls = _walk_urls(item)
    video_url = None
    thumb_url = None
    for ext in (".mp4", ".webm", ".gif"):
        video_url = next((u for u in urls if ext in u.lower()), None)
        if video_url:
            break
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        thumb_url = next((u for u in urls if ext in u.lower()), None)
        if thumb_url:
            break
    if not thumb_url:
        thumb_url = video_url
    return video_url, thumb_url


def _klipy_candidates(query: str, n: int = 4) -> list[dict]:
    if not KLIPY_API_KEY:
        return []
    try:
        r = requests.get(
            f"https://api.klipy.com/api/v1/{KLIPY_API_KEY}/gifs/search",
            params={"q": query, "per_page": max(8, n), "rating": "pg-13", "locale": "en_US"},
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or data.get("results") or data.get("gifs") or []
        if isinstance(items, dict):
            items = list(items.values())
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_url, thumb_url = _pick_klipy_urls(item)
            if video_url and thumb_url:
                candidates.append({
                    "video_url": video_url,
                    "thumb_url": thumb_url,
                    "source": "Klipy"
                })
            if len(candidates) >= n:
                break
        return candidates
    except Exception as e:
        print(f"[B-roll] Klipy search failed for '{query}': {e}")
        return []


def _klipy_video(query: str) -> str | None:
    candidates = _klipy_candidates(query, n=1)
    return candidates[0]["video_url"] if candidates else None


# ── Source 1: Pexels Candidates ──────────────────────────────────────────────

def _pexels_candidates(query: str, orientation: str, n: int = 8) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": query,
                "per_page": min(80, max(n * 3, 15)),
                "orientation": orientation,
                "size": "medium",
            },
            timeout=30,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        candidates = []
        for video in videos:
            image_url = video.get("image")
            video_files = [f for f in video.get("video_files", []) if f.get("quality") in ("hd", "sd")]
            if image_url and video_files:
                video_files.sort(key=lambda f: f.get("width", 0), reverse=True)
                candidates.append({
                    "video_url": video_files[0]["link"],
                    "thumb_url": image_url,
                    "source": "Pexels"
                })
        return candidates
    except Exception as e:
        print(f"[B-roll] Pexels search failed for '{query}': {e}")
        return []


# ── Source 2: Pixabay ────────────────────────────────────────────────────────

def _pixabay_video(query: str) -> str | None:
    if not PIXABAY_API_KEY:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": min(50, max(3 * 3, 10)),
                "order": "popular",
                "safesearch": "true",
                "min_width": 1920
            },
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        videos_data = hits[0].get("videos", {})
        for size in ["large", "medium", "small", "tiny"]:
            url = videos_data.get(size, {}).get("url")
            if url:
                return url
        return None
    except Exception as e:
        print(f"[B-roll] Pixabay failed for '{query}': {e}")
        return None


# ── Source 3: Coverr (cinematic, high quality) ───────────────────────────────

def _coverr_video(query: str) -> str | None:
    if not COVERR_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"keywords": query, "api_key": COVERR_API_KEY, "page": 1, "size": 5, "urls": "true"},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        item = random.choice(hits[:3])
        urls = item.get("urls", {})
        if not urls:
            return None
        video_url = urls.get("mp4_download") or urls.get("mp4")
        if isinstance(video_url, dict):
            video_url = video_url.get("hd") or video_url.get("sd")
        return video_url
    except Exception as e:
        print(f"[B-roll] Coverr failed for '{query}': {e}")
        return None


def _coverr_candidates(query: str, orientation: str, n: int = 5) -> list[dict]:
    if not COVERR_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.coverr.co/videos",
            params={"keywords": query, "api_key": COVERR_API_KEY, "page": 1, "size": n * 3, "urls": "true"},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        candidates = []
        for item in hits:
            thumb = item.get("thumbnail")
            urls = item.get("urls", {})
            if urls:
                video_url = urls.get("mp4_download") or urls.get("mp4")
                if isinstance(video_url, dict):
                    video_url = video_url.get("hd") or video_url.get("sd")
                if thumb and video_url:
                    is_vertical = item.get("is_vertical", False)
                    candidates.append({
                        "video_url": video_url,
                        "thumb_url": thumb,
                        "is_vertical": is_vertical,
                        "source": "Coverr"
                    })
        # Sort candidates to prefer the requested orientation
        if orientation == "portrait":
            candidates.sort(key=lambda x: x["is_vertical"], reverse=True)
        else:
            candidates.sort(key=lambda x: x["is_vertical"], reverse=False)
        return candidates[:n]
    except Exception as e:
        print(f"[B-roll] Coverr candidates search failed for '{query}': {e}")
        return []


def _pixabay_candidates(query: str, n: int = 3) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_API_KEY,
                "q": query,
                "per_page": min(50, max(n * 3, 10)),
                "order": "popular",
                "safesearch": "true",
                "min_width": 1920
            },
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        candidates = []
        for item in hits:
            picture_id = item.get("picture_id")
            thumb = None
            if picture_id:
                thumb = f"https://i.vimeocdn.com/video/{picture_id}_640x360.jpg"
            
            videos_data = item.get("videos", {})
            video_url = None
            for size in ["large", "medium", "small", "tiny"]:
                url = videos_data.get(size, {}).get("url")
                if url:
                    video_url = url
                    break
            if thumb and video_url:
                candidates.append({
                    "video_url": video_url,
                    "thumb_url": thumb,
                    "source": "Pixabay"
                })
        return candidates
    except Exception as e:
        print(f"[B-roll] Pixabay candidates failed for '{query}': {e}")
        return []


def _nasa_video_candidate(query: str) -> dict | None:
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params=_nasa_params(query, "video", 20),
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None
        
        for item in items[:2]:
            nasa_id = item.get("data", [{}])[0].get("nasa_id")
            links = item.get("links", [])
            thumb_url = None
            for link in links:
                if link.get("rel") == "preview" or link.get("render") == "image":
                    thumb_url = link.get("href")
                    break
            if not nasa_id or not thumb_url:
                continue
                
            r_asset = requests.get(
                f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}",
                headers={"User-Agent": "yt-auto/1.0"},
                timeout=15,
            )
            r_asset.raise_for_status()
            items_asset = r_asset.json().get("collection", {}).get("items", [])
            video_url = None
            for a in items_asset:
                href = a.get("href", "")
                if href.endswith("~medium.mp4") or href.endswith("~mobile.mp4"):
                    video_url = href
                    break
            if not video_url:
                for a in items_asset:
                    href = a.get("href", "")
                    if href.endswith(".mp4"):
                        video_url = href
                        break
            if video_url:
                return {
                    "video_url": video_url,
                    "thumb_url": thumb_url,
                    "source": "NASA"
                }
        return None
    except Exception as e:
        print(f"[B-roll] NASA candidate search failed for '{query}': {e}")
        return None


def _wikimedia_video_candidate(query: str) -> dict | None:
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srnamespace": "6",  # File namespace
                "srsearch": f"{query} filetype:video OR filetype:webm OR filetype:ogv",
                "format": "json",
                "srlimit": "3",
            },
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None
  
        for res in results[:2]:
            title = res["title"]
            r_info = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": title,
                    "prop": "imageinfo",
                    "iiprop": "url|thumb",
                    "iiurlwidth": "640",
                    "format": "json",
                },
                headers={"User-Agent": "yt-auto/1.0"},
                timeout=15,
            )
            r_info.raise_for_status()
            pages = r_info.json().get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                imageinfo = page_data.get("imageinfo", [])
                if imageinfo:
                    video_url = imageinfo[0].get("url")
                    thumb_url = imageinfo[0].get("thumburl")
                    if video_url and thumb_url:
                        return {
                            "video_url": video_url,
                            "thumb_url": thumb_url,
                            "source": "Wikimedia"
                        }
        return None
    except Exception as e:
        print(f"[B-roll] Wikimedia video candidate failed for '{query}': {e}")
        return None





# ── Source 4: NASA Image & Video Library (no key — public domain) ─────────────

def _nasa_image(query: str) -> str | None:
    """Fetches a real NASA image for science/space topics. Completely free, no key."""
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params={
                **_nasa_params(query, "image", 5),
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None
        item = random.choice(items[:3])
        links = item.get("links", [])
        for link in links:
            href = link.get("href", "")
            if href and href.startswith("http"):
                return href
        return None
    except Exception as e:
        print(f"[B-roll] NASA failed for '{query}': {e}")
        return None


# ── Source 5: Wikipedia article thumbnail ────────────────────────────────────

def _wikipedia_image(query: str) -> str | None:
    """
    Fetches the Wikipedia article image for the query topic.
    No API key required. Perfect for named people and well-known concepts.
    """
    try:
        title = urllib.parse.quote(query.replace(" ", "_"))
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # Prefer full-size original, fall back to thumbnail
        img = data.get("originalimage", {}).get("source") \
           or data.get("thumbnail", {}).get("source")
        return img
    except Exception as e:
        print(f"[B-roll] Wikipedia failed for '{query}': {e}")
        return None


def _wikimedia_video(query: str) -> str | None:
    """Search Wikimedia Commons for CC-licensed educational videos and fetch actual URL. No API key needed."""
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srnamespace": "6",  # File namespace
                "srsearch": f"{query} filetype:video OR filetype:webm OR filetype:ogv",
                "format": "json",
                "srlimit": "5",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None

        # Pick the top result and use Wikipedia API to get the correct URL
        title = results[0]["title"]
        r_info = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_info.raise_for_status()
        pages = r_info.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            imageinfo = page_data.get("imageinfo", [])
            if imageinfo:
                return imageinfo[0].get("url")
        return None
    except Exception as e:
        print(f"[B-roll] Wikimedia Commons failed for '{query}': {e}")
        return None


def _dvids_candidates(query: str, n: int = 3) -> list[dict]:
    try:
        r = requests.get(
            "https://www.dvidshub.net/api/search",
            params={"query": query, "type": "video", "rows": n * 3, "output": "json"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; yt-auto/1.0)"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        out = []
        for item in results:
            v = item.get("download_url") or item.get("file_url")
            t = item.get("thumbnail_url") or item.get("image_url")
            title = item.get("title", "")
            id_val = item.get("id")
            if v and t:
                out.append({
                    "video_url": v,
                    "thumb_url": t,
                    "source": "DVIDS",
                    "title": title,
                    "id": id_val,
                    "width": 1920
                })
        return out[:n]
    except Exception as e:
        print(f"[B-roll] DVIDS search failed for '{query}': {e}")
        return []

def _dvids_video(query: str) -> str | None:
    candidates = _dvids_candidates(query, n=1)
    return candidates[0]["video_url"] if candidates else None

def _openverse_image(query: str) -> str | None:
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "license": "cc0,by", "page_size": 5, "orientation": "landscape"},
            headers={"User-Agent": "yt-auto/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        chosen = random.choice(results[:3])
        return chosen.get("url")
    except Exception as e:
        print(f"[B-roll] Openverse image search failed for '{query}': {e}")
        return None

def _archive_candidates(query: str, n: int = 3) -> list[dict]:
    import urllib.parse
    headers = {"User-Agent": "yt-auto/1.0 (educational-pipeline)"}
    candidates = []

    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"collection:prelinger AND ({query})",
                "fl[]": ["identifier", "title", "downloads"],
                "sort[]": "downloads desc",
                "rows": n * 4,
                "output": "json"
            },
            headers=headers,
            timeout=20
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"[B-roll] Archive Prelinger search failed for '{query}': {e}")
        docs = []

    if not docs:
        try:
            r = requests.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": f"({query}) AND mediatype:movies",
                    "fl[]": ["identifier", "title", "downloads"],
                    "sort[]": "downloads desc",
                    "rows": n * 4,
                    "output": "json"
                },
                headers=headers,
                timeout=20
            )
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
        except Exception as e:
            print(f"[B-roll] Archive broader search failed for '{query}': {e}")
            docs = []

    for doc in docs:
        if len(candidates) >= n:
            break
        identifier = doc.get("identifier")
        title = doc.get("title", "")
        if not identifier:
            continue
        try:
            r_files = requests.get(
                f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
                headers=headers,
                timeout=15
            )
            r_files.raise_for_status()
            files = r_files.json().get("files", [])
            
            video_url = None
            for f in files:
                name = f.get("name", "")
                if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
                    video_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
                    break
            
            if not video_url:
                continue
                
            thumb_url = None
            for f in files:
                name = f.get("name", "")
                if name.endswith("__ia_thumb.jpg") or name.lower().endswith((".jpg", ".png", ".jpeg")):
                    thumb_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
                    break
            if not thumb_url:
                thumb_url = f"https://archive.org/services/img/{identifier}"
                
            candidates.append({
                "video_url": video_url,
                "thumb_url": thumb_url,
                "source": "Archive",
                "title": title,
                "id": identifier
            })
        except Exception as e:
            print(f"[B-roll] Archive metadata fetch failed for '{identifier}': {e}")
            
    return candidates


def _nasa_video(query: str) -> str | None:
    """Fetches a real NASA video for science/space topics. Completely free, no key."""
    try:
        r = requests.get(
            "https://images-api.nasa.gov/search",
            params={
                **_nasa_params(query, "video", 5),
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        if not items:
            return None

        # Pick one from top 3
        item = random.choice(items[:3])
        nasa_id = item.get("data", [{}])[0].get("nasa_id")
        if not nasa_id:
            return None

        r_asset = requests.get(
            f"https://images-api.nasa.gov/asset/{urllib.parse.quote(nasa_id)}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_asset.raise_for_status()
        items_asset = r_asset.json().get("collection", {}).get("items", [])
        for a in items_asset:
            href = a.get("href", "")
            if href.endswith("~medium.mp4") or href.endswith("~mobile.mp4"):
                return href
        for a in items_asset:
            href = a.get("href", "")
            if href.endswith(".mp4"):
                return href
        return None
    except Exception as e:
        print(f"[B-roll] NASA video failed for '{query}': {e}")
        return None


def _archive_video(query: str) -> str | None:
    """Search Internet Archive for public domain movies. No API key needed."""
    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"collection:prelinger AND title:({query})",
                "fl[]": "identifier",
                "rows": "5",
                "output": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if docs:
            identifier = docs[0]["identifier"]
            r_files = requests.get(
                f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
                headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
                timeout=15,
            )
            r_files.raise_for_status()
            files = r_files.json().get("files", [])
            for f in files:
                name = f.get("name", "")
                if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
                    return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
    except Exception as e:
        print(f"[B-roll] Prelinger filter search failed for '{query}': {e}")

    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"title:({query}) AND mediatype:(movies)",
                "fl[]": "identifier",
                "rows": "5",
                "output": "json",
            },
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=20,
        )
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if not docs:
            return None

        identifier = docs[0]["identifier"]
        r_files = requests.get(
            f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
            headers={"User-Agent": "yt-auto/1.0 (educational-pipeline)"},
            timeout=15,
        )
        r_files.raise_for_status()
        files = r_files.json().get("files", [])
        for f in files:
            name = f.get("name", "")
            if (name.endswith(".mp4") or name.endswith(".webm") or name.endswith(".mkv") or name.endswith(".avi")) and int(f.get("size", 0)) > 10_000:
                return f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}"
        return None
    except Exception as e:
        print(f"[B-roll] Internet Archive failed for '{query}': {e}")
        return None


def _parse_iso_duration(duration_str: str) -> float:
    import re
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0.0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return float(hours * 3600 + minutes * 60 + seconds)


def _youtube_candidates(query: str, n: int = 5) -> list[dict]:
    """
    Search YouTube for matchable B-roll clips using broad ytsearch query.
    Captures uploader channel handle for on-screen Fair Use attribution.
    """
    import yt_dlp
    import urllib.parse
    import re
    
    candidates = []
    seen_urls = set()
    search_queries = [f"{query} footage", query, f"{query} 4k"]
    
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': False,
    }
    
    for sq in search_queries:
        if len(candidates) >= n:
            break
        try:
            print(f"[B-roll] Searching YouTube for: '{sq}'...")
            search_target = f"ytsearch{n*2}:{sq}"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(search_target, download=False)
                entries = result.get('entries', []) if result else []
                
                for entry in entries:
                    if not entry:
                        continue
                    title = entry.get('title', '')
                    url = entry.get('url', '')
                    duration = entry.get('duration')
                    
                    duration_secs = float(duration) if duration else 0.0
                    if duration_secs > 0.0 and (duration_secs < 12.0 or duration_secs > 1800.0):
                        continue
                    
                    # Filter out lecture/classroom/blackboard/explainer/text-heavy titles unless explicitly requested
                    title_lower = title.lower()
                    bad_title_keywords = [
                        "lecture", "classroom", "blackboard", "chalkboard", "whiteboard", "tutorial", "course",
                        "teacher", "presentation", "lesson", "explained", "visually", "visualized", "breakdown",
                        "guide", "how to", "free stock", "stock footage", "watermark", "videohive", "shutterstock",
                        "stocksubmitter", "knot9", "depositphotos", "dreamstime", "getty", "pond5", "envato", "preview",
                        "istock", "download", "text", "subtitles", "slides", "powerpoint", "explainer", "overview",
                        "infographic", "diagram", "illustration", "chart", "diagrams", "still", "figure", "textbook",
                        "green screen", "chroma key", "greenscreen", "smartphone", "holding phone", "phone screen",
                        "mobile screen", "scrolling", "mockup", "vertical smartphone", "mobile phone", "app review"
                    ]
                    if any(bad in title_lower for bad in bad_title_keywords):
                        print(f"[B-roll] Skipping text/explainer/classroom/greenscreen candidate: '{title}'")
                        continue
                    
                    video_id = entry.get('id')
                    if not video_id and url:
                        m_id = re.search(r'(?:v=|\/)([^&\n?#]+)', url)
                        if m_id:
                            video_id = m_id.group(1)
                    
                    if not video_id:
                        continue
                        
                    full_url = f"https://www.youtube.com/watch?v={video_id}"
                    if full_url in seen_urls:
                        continue
                    seen_urls.add(full_url)
                    
                    uploader = entry.get('uploader') or entry.get('channel') or entry.get('uploader_id') or "YouTube"
                    clean_uploader = re.sub(r'[^a-zA-Z0-9_-]', '', str(uploader))
                    handle = f"@{clean_uploader}" if clean_uploader else "@YouTube"
                    
                    thumb_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                    
                    candidates.append({
                        "source": "YouTube",
                        "video_url": full_url,
                        "thumb_url": thumb_url,
                        "title": title,
                        "description": entry.get('description', '') or "",
                        "duration": duration_secs,
                        "uploader_name": uploader,
                        "uploader_handle": handle
                    })
                    
                    if len(candidates) >= n:
                        break
        except Exception as e:
            print(f"[B-roll] YouTube search failed for '{sq}': {e}")
            
    print(f"[B-roll] Found {len(candidates)} YouTube candidate clips.")
    return candidates


def _download_video_robust(url: str, out_path: str, segment_index: int, candidate_info: dict | None = None) -> bool:
    try:
        # Check if downloading from YouTube
        if "youtube.com" in url or "youtu.be" in url:
            print(f"[B-roll] Downloading YouTube video slice using yt-dlp CLI: {url}...")
            
            # 1. Fetch metadata json to read duration and uploader details
            duration_secs = 0.0
            info = {}
            try:
                cmd_info = ["yt-dlp", "--dump-json", "--no-check-certificates", url]
                res_info = subprocess.run(cmd_info, capture_output=True, text=True, check=True)
                info = json.loads(res_info.stdout)
                duration_secs = float(info.get("duration", 0.0))
            except Exception as e:
                print(f"[B-roll] Warning: Could not retrieve video duration: {e}")
            
            # 2. Pick a safe start time to skip intro card/logos
            start_time = 20.0
            if duration_secs > 0.0 and duration_secs <= 25.0:
                start_time = 2.0
            if duration_secs > 0.0 and duration_secs <= 5.0:
                start_time = 0.0
                
            end_time = start_time + 10.0 # download 10 seconds slice
            section_arg = f"*{start_time}-{end_time}"
            
            # 3. Call yt-dlp CLI with Android player client to bypass CI IP blocks
            cmd_dl = [
                "yt-dlp",
                "--download-sections", section_arg,
                "--extractor-args", "youtube:player_client=android,web,mweb",
                "--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "--merge-output-format", "mp4",
                "--user-agent", "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
                "--no-check-certificates",
                "--retries", "3",
                "--output", out_path,
                url
            ]
            print(f"[B-roll] Running yt-dlp section download: {' '.join(cmd_dl)}")
            subprocess.run(cmd_dl, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Check if downloaded directly or saved with alternate container extension
            success = os.path.exists(out_path) and os.path.getsize(out_path) > 10_000
            if not success:
                for alt_ext in [".mp4", ".webm", ".mkv"]:
                    candidate_file = out_path + alt_ext
                    if os.path.exists(candidate_file) and os.path.getsize(candidate_file) > 10_000:
                        print(f"[B-roll] Converting/renaming container {candidate_file} -> {out_path}...")
                        cmd_conv = [
                            "ffmpeg", "-y", "-i", candidate_file,
                            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-an", out_path
                        ]
                        subprocess.run(cmd_conv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if os.path.exists(candidate_file):
                            os.remove(candidate_file)
                        break
            success = os.path.exists(out_path) and os.path.getsize(out_path) > 10_000
            if success:
                # Verify actual duration with ffprobe to prevent short repeating clips or corrupt downloads
                try:
                    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", out_path]
                    res_dur = subprocess.run(cmd_dur, capture_output=True, text=True, timeout=10)
                    act_dur = float(res_dur.stdout.strip())
                    if act_dur > 0.0 and act_dur < 6.0:
                        print(f"[B-roll] Downloaded slice too short ({act_dur:.2f}s < 6.0s). Rejecting candidate to prevent repeating clip loop.")
                        if os.path.exists(out_path):
                            os.remove(out_path)
                        return False
                except Exception as e_dur:
                    print(f"[B-roll] Warning: Could not verify downloaded clip duration or corrupt video ({e_dur}). Testing container integrity...")
                    try:
                        test_cmd = ["ffmpeg", "-v", "error", "-i", out_path, "-t", "1", "-f", "null", "-"]
                        test_res = subprocess.run(test_cmd, capture_output=True, timeout=10)
                        if test_res.returncode != 0:
                            print(f"[B-roll] Video container corrupted. Rejecting candidate.")
                            if os.path.exists(out_path):
                                os.remove(out_path)
                            return False
                    except Exception:
                        if os.path.exists(out_path):
                            os.remove(out_path)
                        return False

                # Save credit metadata for on-screen attribution in phase 7
                uploader = info.get("uploader") or info.get("channel") or (candidate_info.get("uploader_name") if candidate_info else "YouTube")
                handle = candidate_info.get("uploader_handle") if candidate_info else None
                if not handle and uploader:
                    clean_u = re.sub(r'[^a-zA-Z0-9_-]', '', str(uploader))
                    handle = f"@{clean_u}"
                
                credit_data = {
                    "source": "YouTube",
                    "uploader_name": uploader,
                    "uploader_handle": handle or "@YouTube",
                    "video_url": url,
                    "title": info.get("title") or (candidate_info.get("title") if candidate_info else "")
                }
                credit_file = f"output/broll_{segment_index}_credit.json"
                try:
                    with open(credit_file, "w") as cf:
                        json.dump(credit_data, cf)
                    print(f"[B-roll] Saved segment {segment_index} credit metadata: {handle}")
                except Exception as cerr:
                    print(f"[B-roll] Warning: Could not save credit file: {cerr}")
            return success

        r = requests.get(url, stream=True, timeout=90, headers={"User-Agent": "yt-auto/1.0"})
        r.raise_for_status()

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        is_webm = path.endswith(".webm") or path.endswith(".ogv")
        is_gif = path.endswith(".gif")

        temp_ext = ".webm" if is_webm else ".gif" if is_gif else ".mp4"
        temp_file = f"output/temp_dl_{segment_index}{temp_ext}"
        with open(temp_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if os.path.exists(temp_file) and os.path.getsize(temp_file) > 10_000:
            if is_webm or is_gif:
                print(f"[B-roll] Converting {temp_ext} from {url} to mp4...")
                cmd = [
                    "ffmpeg", "-y", "-i", temp_file,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-an", out_path
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 10_000
            else:
                if os.path.exists(out_path):
                    os.remove(out_path)
                os.rename(temp_file, out_path)
                return True
        return False
    except Exception as e:
        print(f"[B-roll] Robust download failed for {url}: {e}")
        return False


# ── Ken Burns zoom — applied to ALL image-to-video conversions ───────────────

def _shorten_narration(text: str, max_words: int = 10) -> str:
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def _image_to_ken_burns_video(img_path: str, out_path: str, w: int, h: int, duration: float = 6.0, niche: str = "general", caption: str = ""):
    """
    Converts a static image or video clip to a normalized video.
    If input is already a video asset, normalizes directly with FFmpeg.
    If input is an image, tries Hyperframes for motion overlays, falling back to Ken Burns zoompan.
    """
    ext = os.path.splitext(img_path)[1].lower()
    is_video = ext in [".mp4", ".webm", ".ogv", ".mov", ".avi"] or "video" in img_path.lower() or img_path.endswith("_video")
    
    if is_video:
        print(f"[B-roll] Normalizing video asset: {img_path} -> {out_path}")
        cmd = [
            "ffmpeg", "-y", "-i", img_path,
            "-vf", f"scale=trunc({w}/2)*2:trunc({h}/2)*2:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1",
            "-t", str(duration), "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-an", out_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    try:
        if os.environ.get("DISABLE_HYPERFRAMES", "0") == "1":
            raise RuntimeError("Hyperframes disabled via DISABLE_HYPERFRAMES")
        import uuid
        import shutil
        
        niche_map = {
            "science": "science",
            "nature": "nature",
            "mystery": "nature",
            "engineering": "engineering",
            "business": "business",
            "general": "general"
        }
        mapped_niche = niche_map.get(niche, "general")
        
        abs_img = os.path.abspath(img_path)
        abs_out = os.path.abspath(out_path)
        template_dir = os.path.abspath("pipeline/hyperframes_templates")
        
        # Copy input asset to template directory to avoid CORS/Same-Origin file:// loading blocks in Puppeteer/Chrome
        temp_filename = f"temp_{uuid.uuid4().hex}{ext}"
        temp_path = os.path.join(template_dir, temp_filename)
        shutil.copy2(img_path, temp_path)
        
        variables = {
            "imageUrl": temp_filename,
            "duration": duration,
            "niche": mapped_niche,
            "caption": caption
        }
        
        resolution = "portrait" if h > w else "landscape"
        template_file = "index_portrait.html" if h > w else "index_landscape.html"
        cmd = [
            "npx", "-y", "hyperframes", "render", template_dir,
            "-c", template_file,
            "--output", abs_out,
            "--resolution", resolution,
            "--quality", "high",
            "--variables", json.dumps(variables)
        ]
        
        print(f"[B-roll] Rendering Hyperframes with niche={mapped_niche}...")
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
                print(f"[B-roll] Hyperframes render successful: {out_path}")
                return
            else:
                print("[B-roll] Hyperframes render failed or returned empty file. Falling back to FFmpeg.")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
    except Exception as e:
        if "Hyperframes disabled" in str(e):
            print("[B-roll] Hyperframes disabled via DISABLE_HYPERFRAMES. Using FFmpeg directly.")
        else:
            print(f"[B-roll] Hyperframes execution error: {e}. Falling back to FFmpeg.")

    fps    = 30
    frames = int(duration * fps)

    styles = [
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}",
        f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:x=0:y=0:s={w}x{h}:fps={fps}",
        f"scale=8000:-1,zoompan=z='min(zoom+0.001,1.3)':d={frames}:x='iw-iw/zoom':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}",
    ]
    vf = random.choice(styles)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path,
        "-vf", f"{vf},setsar=1",
        "-t", str(duration), "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-an", out_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



# ── Fallback: Pollinations.ai (AI-generated, multiple models) ────────────────

def _pollinations_image(query: str, w: int, h: int, img_path: str) -> bool:
    """Returns True if cinematic stock image was downloaded successfully via Unsplash or Pollinations AI."""
    clean_q = re.sub(r'[^a-zA-Z0-9\s]', '', query).strip()
    words = [w for w in clean_q.split() if len(w) > 2]
    topic_tag = "%20".join(words[:3]) if words else "nature"
    
    # 1. Try Unsplash Direct HD Image Endpoint
    try:
        unsplash_url = f"https://images.unsplash.com/photo-1518709268805-4e9042af9f23?w={w}&h={h}&fit=crop"
        # Or search endpoint
        unsplash_search = f"https://source.unsplash.com/1080x1920/?{topic_tag}"
        r = requests.get(unsplash_search, timeout=8, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 10_000:
            with open(img_path, "wb") as f:
                f.write(r.content)
            print(f"[B-roll] Unsplash image download OK for query '{topic_tag}'.")
            return True
    except Exception as e:
        print(f"[B-roll] Unsplash search failed: {e}")

    # 2. Try Pollinations AI with clean prompt
    encoded_prompt = urllib.parse.quote(f"4k cinematic documentary footage of {clean_q}, hyperrealistic, 8k, detailed, photorealistic, no text, no watermark")
    for model in ["flux", "turbo"]:
        try:
            seed = random.randint(1, 100000)
            url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width={w}&height={h}&model={model}&nologo=true&seed={seed}"
            )
            r = requests.get(url, timeout=12)
            if r.status_code == 200 and len(r.content) > 10_000:
                with open(img_path, "wb") as f:
                    f.write(r.content)
                print(f"[B-roll] Pollinations {model} OK.")
                return True
        except Exception as e:
            print(f"[B-roll] Pollinations {model} failed: {e}")
    return False


# ── Last resort: PIL gradient placeholder ────────────────────────────────────

def _pil_placeholder(query: str, w: int, h: int, img_path: str):
    """Better-looking placeholder: dark gradient with large centered text."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    # Dark gradient background (top dark blue → bottom near-black)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        ratio = y / h
        arr[y, :, 0] = int(10 + ratio * 5)   # R
        arr[y, :, 1] = int(10 + ratio * 20)   # G
        arr[y, :, 2] = int(40 + ratio * 20)   # B

    img  = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)

    # Draw centered query text, large and readable
    words  = query.upper().split()
    lines  = []
    line   = ""
    for word in words:
        test = (line + " " + word).strip()
        if len(test) > 18:
            lines.append(line.strip())
            line = word
        else:
            line = test
    if line:
        lines.append(line.strip())

    font_size = max(60, min(100, w // (max(len(l) for l in lines) + 1) if lines else 80))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    total_text_h = len(lines) * (font_size + 10)
    y_start      = (h - total_text_h) // 2

    for i, line_text in enumerate(lines):
        bbox = draw.textbbox((0, 0), line_text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = (w - tw) // 2
        y    = y_start + i * (font_size + 10)
        # Shadow
        draw.text((x + 3, y + 3), line_text, font=font, fill=(0, 0, 0))
        # Main text
        draw.text((x, y), line_text, font=font, fill=(255, 255, 255))

    img.save(img_path, "JPEG", quality=90)


def _make_clean_fallback(query: str) -> str:
    stop_words = {
        "failure", "failed", "failed", "breaking", "broken", "broke",
        "damaged", "damage", "collapsed", "collapse", "slipping", "slipped",
        "slip", "during", "mechanism", "problems", "problem", "defect",
        "defective", "faulty", "error", "issue", "issues", "accident",
        "disaster", "ruined", "destroy", "destroyed", "destroying",
        "a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "of"
    }
    words = query.lower().split()
    filtered = [w for w in words if w not in stop_words]
    if filtered:
        return " ".join(filtered)
    return query


def _get_video_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filepath
    ]
    try:
        import subprocess
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        return 0.0

# ── Master fetch function ────────────────────────────────────────────────────


def _extract_collage_to_file(video_path: str, out_path: str) -> bool:
    try:
        from PIL import Image
        import subprocess
        # Get video duration
        cmd_dur = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        duration = float(subprocess.check_output(cmd_dur).decode().strip())
        if duration <= 0:
            return False
            
        # Extract 3 frames past initial 1.5s intro slide (at 25%, 55%, 85% of remaining duration)
        start_offset = 1.5 if duration > 4.0 else 0.0
        rem_dur = max(1.0, duration - start_offset)
        timestamps = [start_offset + rem_dur * 0.25, start_offset + rem_dur * 0.55, start_offset + rem_dur * 0.85]
        frames = []
        import numpy as np
        
        for idx, ts in enumerate(timestamps):
            temp_frame = f"{video_path}_collage_f_{idx}.jpg"
            # Extract frame at ts
            cmd = [
                "ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", video_path,
                "-vframes", "1", "-f", "image2", temp_frame
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if os.path.exists(temp_frame):
                try:
                    img = Image.open(temp_frame).convert("RGB")
                    arr = np.array(img)
                    mean_lum = float(np.mean(arr))
                    std_lum = float(np.std(arr))
                    # Reject black screens, dark loading screens, or white flash screens
                    if mean_lum < 15.0 or mean_lum > 245.0 or std_lum < 8.0:
                        print(f"[B-roll] Rejecting dark/flash frame at {ts:.2f}s (mean_lum={mean_lum:.1f}, std={std_lum:.1f})")
                        os.remove(temp_frame)
                        continue
                    # Resize to keep aspect ratio but limit size (e.g. height 240)
                    img.thumbnail((320, 240))
                    frames.append((img, temp_frame))
                except Exception:
                    if os.path.exists(temp_frame):
                        os.remove(temp_frame)
                        
        if not frames:
            return False
            
        # Stitch frames horizontally
        widths, heights = zip(*(f[0].size for f in frames))
        total_width = sum(widths)
        max_height = max(heights)
        
        collage = Image.new('RGB', (total_width, max_height))
        x_offset = 0
        for img, path in frames:
            collage.paste(img, (x_offset, 0))
            x_offset += img.size[0]
            # Clean up temp frame
            os.remove(path)
            
        collage.save(out_path, "JPEG", quality=80)
        return True
    except Exception as e:
        print(f"[B-roll] Failed to create collage for {video_path}: {e}")
        return False


def _expand_query(query: str, channel: str, n: int = 5) -> list[str]:
    from pipeline.gemini import _post_with_rotation
    from pipeline.config import GEMINI_API_BASE, GEMINI_FLASH
    try:
        prompt_text = (
            f"You are a professional video stock researcher. Query: '{query}'. Channel Niche: {channel}.\n"
            f"Generate {n} SHORT, CONCRETE stock footage search terms (2-4 words maximum).\n"
            f"CRITICAL RULES:\n"
            f"1. Use ONLY concrete physical objects, settings, or human actions (e.g. 'microscope lab scientist', 'blue ocean coral reef', 'engine piston moving').\n"
            f"2. NEVER use abstract words like 'concept', 'breakthrough', 'discovery', 'mind-blowing', 'chemical' (alone), 'important'.\n"
            f"3. Focus on real-world visual symbols, settings, or close-ups that represent '{query}'.\n"
            f"Return ONLY a JSON array of strings."
        )
        url = f"{GEMINI_API_BASE}/models/{GEMINI_FLASH}:generateContent?key={{key}}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        }
        resp = _post_with_rotation(url, payload, timeout=30)
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(raw)
        if isinstance(items, list):
            res = []
            for item in items:
                if isinstance(item, str):
                    s = item.strip()
                    if s and s.lower() != query.lower():
                        res.append(s)
            seen = set()
            deduped = []
            for item in res:
                if item.lower() not in seen:
                    seen.add(item.lower())
                    deduped.append(item)
            return deduped
        return []
    except Exception:
        return []


def _score_candidate(item: dict, query: str, target_duration: float = 8.0) -> float:
    text_to_check = ""
    for field in ["title", "tags", "video_url", "thumb_url"]:
        val = item.get(field)
        if isinstance(val, str):
            text_to_check += " " + val
        elif isinstance(val, list):
            text_to_check += " " + " ".join(str(v) for v in val)
            
    query_words = [w.strip(",.?!:;-()\"'").lower() for w in query.split()]
    query_words = [w for w in query_words if len(w) > 2]
    if not query_words:
        query_words = [w.strip(",.?!:;-()\"'").lower() for w in query.split() if w]
        
    overlap_score = 0.0
    if query_words:
        matches = sum(1 for w in query_words if w in text_to_check.lower())
        overlap_score = (matches / len(query_words)) * 30.0
        
    width = item.get("width")
    height = item.get("height")
    res_score = 5.0
    url_str = str(item.get("video_url", "")).lower()
    
    if isinstance(width, (int, float)) and width > 0:
        if width >= 3840:
            res_score = 25.0
        elif width >= 1920:
            res_score = 20.0
        elif width >= 1280:
            res_score = 10.0
        else:
            res_score = 5.0
    elif isinstance(height, (int, float)) and height > 0:
        if height >= 2160:
            res_score = 25.0
        elif height >= 1080:
            res_score = 20.0
        elif height >= 720:
            res_score = 10.0
        else:
            res_score = 5.0
    else:
        if "4k" in url_str or "2160p" in url_str:
            res_score = 25.0
        elif "1080p" in url_str or "1920" in url_str or "hd" in url_str:
            res_score = 20.0
        elif "720p" in url_str or "1280" in url_str:
            res_score = 10.0
        else:
            res_score = 5.0
            
    dur_score = 10.0
    item_dur = item.get("duration")
    if isinstance(item_dur, (int, float)) and item_dur > 0:
        diff = abs(item_dur - target_duration)
        dur_score = max(0.0, 20.0 - 2.0 * diff)
        
    source_weights = {
        "youtube": 60.0,
        "nasa": 20.0,
        "dvids": 18.0,
        "wikimedia": 16.0,
        "archive": 14.0,
        "coverr": 18.0,
        "pexels": 15.0,
        "pixabay": 14.0,
        "klipy": 8.0
    }
    source_lower = str(item.get("source", "")).lower()
    source_score = source_weights.get(source_lower, 10.0)
    
    return float(overlap_score + res_score + dur_score + source_score)


def sanitize_broll_query(query: str) -> str:
    """
    Sanitize B-roll query by removing abstract adjectives, verbs, and meta-descriptions
    that break YouTube and stock search API index matching.
    """
    if not query:
        return ""
    q_clean = re.sub(r'\bcross[-_\s]+section\b', '', query, flags=re.IGNORECASE)
    noise_words = {
        "animated", "animation", "defect", "defective", "dramatic", "unraveling", "stuck",
        "cross", "section", "burst", "shattered", "shatter", "betraying", "secret", "flaw",
        "concept", "visualization", "illustration", "rendering", "cgi", "showing", "display",
        "unearthing", "typing", "close", "up", "closeup", "jarring", "macro", "loop",
        "pumping", "filtering", "survival", "municipal", "how", "why", "system", "process"
    }
    words = re.findall(r'[a-zA-Z0-9]+', q_clean.lower())
    clean_words = [w for w in words if w not in noise_words and len(w) > 2]
    if not clean_words:
        fallback = [w for w in re.findall(r'[a-zA-Z0-9]+', query) if len(w) > 2]
        return " ".join(fallback[:3]) if fallback else query
    return " ".join(clean_words[:4])


def _sanitize_broll_query(query: str) -> str:
    return sanitize_broll_query(query)


def fetch_broll(query: str, format_type: str, segment_index: int, duration: float = 6.0, narration: str = "", alt_queries: list[str] | None = None, used_urls: set[str] | None = None, channel: str = "general") -> str:
    """
    Unified B-roll candidate ranking across multiple platforms (YouTube CC prioritized, Coverr, Pexels, Pixabay, NASA, Wikimedia)
    using Gemini Vision matching and URL de-duplication.
    """
    orientation = "portrait" if format_type == "short" else "landscape"
    out_path    = f"output/broll_{segment_index}.mp4"
    img_path    = f"output/broll_{segment_index}.jpg"
    w, h        = (1080, 1920) if format_type == "short" else (1920, 1080)
    budget_default = "180" if format_type == "short" else "240"
    budget_seconds = int(os.environ.get("BROLL_SEGMENT_BUDGET_SECONDS", budget_default))
    deadline = time.monotonic() + budget_seconds

    def budget_exceeded() -> bool:
        if time.monotonic() <= deadline:
            return False
        print(f"[B-roll] Segment {segment_index}: time budget exceeded ({budget_seconds}s). Using fast fallback.")
        return True

    os.makedirs("output", exist_ok=True)

    # Return cached clip if already valid
    if os.path.exists(out_path) and os.path.getsize(out_path) > 10_000:
        print(f"[B-roll] Segment {segment_index}: using cached clip.")
        return out_path

    # Build fallback queries with sanitization
    sanitized_q = _sanitize_broll_query(query)
    clean_fallback = _make_clean_fallback(query)
    
    queries_to_try = [sanitized_q, query, clean_fallback]
    if alt_queries:
        for q in alt_queries:
            sq = _sanitize_broll_query(q)
            if sq not in queries_to_try:
                queries_to_try.append(sq)
            if q not in queries_to_try:
                queries_to_try.append(q)
        
    clean_words = clean_fallback.split()
    if len(clean_words) > 2:
        general_fallback = " ".join(clean_words[:2])
        if general_fallback not in queries_to_try:
            queries_to_try.append(general_fallback)

    if not budget_exceeded():
        expanded = _expand_query(query, channel=channel, n=5)
        queries_to_try.extend(expanded)

    # Deduplicate final query list (case-insensitive while preserving order)
    seen_q = set()
    queries_to_try_dedup = []
    for q in queries_to_try:
        if q.lower() not in seen_q:
            seen_q.add(q.lower())
            queries_to_try_dedup.append(q)
    queries_to_try = queries_to_try_dedup

    # Gather candidate video metadata from platforms in parallel
    candidates = []

    CHANNEL_SOURCE_PRIORITY = {
        "science":     ["youtube", "nasa", "dvids", "wikimedia", "coverr", "archive", "pexels", "pixabay"],
        "nature":      ["youtube", "pexels", "pixabay", "coverr", "wikimedia", "archive"],
        "mystery":     ["youtube", "archive", "wikimedia", "coverr", "pexels", "pixabay"],
        "engineering": ["youtube", "nasa", "dvids", "coverr", "wikimedia", "pexels", "archive"],
        "business":    ["youtube", "coverr", "pexels", "pixabay", "klipy"],
        "general":     ["youtube", "coverr", "pexels", "pixabay", "nasa", "wikimedia", "archive", "dvids"],
    }

    def run_source_query(source: str, q: str) -> list[dict]:
        try:
            if source == "youtube":
                return _youtube_candidates(q, n=5)
            elif source == "nasa":
                if not NASA_BROLL_ENABLED:
                    return []
                cand = _nasa_video_candidate(q)
                return [cand] if cand else []
            elif source == "wikimedia":
                cand = _wikimedia_video_candidate(q)
                return [cand] if cand else []
            elif source == "dvids":
                return _dvids_candidates(q, n=3)
            elif source == "coverr":
                if not COVERR_API_KEY:
                    return []
                return _coverr_candidates(q, orientation, n=2)
            elif source == "klipy":
                if not KLIPY_API_KEY:
                    return []
                return _klipy_candidates(q, n=2)
            elif source == "pexels":
                if not PEXELS_API_KEY:
                    return []
                return _pexels_candidates(q, orientation, n=2)
            elif source == "pixabay":
                if not PIXABAY_API_KEY:
                    return []
                return _pixabay_candidates(q, n=2)
            elif source == "archive":
                return _archive_candidates(q, n=3)
        except Exception as e:
            print(f"[B-roll] Source {source} query '{q}' failed: {e}")
        return []

    sources = CHANNEL_SOURCE_PRIORITY.get(channel, CHANNEL_SOURCE_PRIORITY["general"])
    tasks = []
    for source in sources:
        for q in queries_to_try[:6]:
            tasks.append((source, q))

    seen_gathering = set()
    source_counts = {src: 0 for src in sources}

    remaining_budget = max(1.0, deadline - time.monotonic())
    timeout = min(45, int(remaining_budget * 0.5))
    if timeout < 1:
        timeout = 1

    print(f"[B-roll] Segment {segment_index}: starting parallel candidate gathering with timeout={timeout}s for sources: {sources}...")

    with ThreadPoolExecutor(max_workers=min(12, len(tasks))) as executor:
        future_to_info = {}
        for source, q in tasks:
            f = executor.submit(run_source_query, source, q)
            future_to_info[f] = (source, q)

        try:
            for future in as_completed(future_to_info.keys(), timeout=timeout):
                source, q = future_to_info[future]
                try:
                    res = future.result()
                    if res:
                        added_count = 0
                        for cand in res:
                            if not isinstance(cand, dict):
                                continue
                            v_url = cand.get("video_url")
                            if v_url and v_url not in seen_gathering:
                                seen_gathering.add(v_url)
                                if "source" not in cand:
                                    cand["source"] = source
                                candidates.append(cand)
                                added_count += 1
                        source_counts[source] += added_count
                except Exception as e:
                    print(f"[B-roll] Future failed for source {source} query '{q}': {e}")
        except Exception as e:
            if "TimeoutError" in type(e).__name__:
                print(f"[B-roll] Parallel gathering timed out after {timeout} seconds.")
            else:
                print(f"[B-roll] Error during parallel gathering: {e}")

    for src in sources:
        print(f"[B-roll] Source '{src}' returned {source_counts[src]} unique candidates.")

    # Apply de-duplication: filter out candidates that have already been used
    if used_urls:
        original_count = len(candidates)
        candidates = [c for c in candidates if c["video_url"] not in used_urls]
        if len(candidates) < original_count:
            print(f"[B-roll] De-duplicated candidates: filtered out {original_count - len(candidates)} already used clips.")

    # Score all candidates
    for c in candidates:
        c["_score"] = _score_candidate(c, query, target_duration=duration)

    # Sort descending by score
    candidates.sort(key=lambda x: x.get("_score", 0.0), reverse=True)

    # Send only the top 8 to vision_rank_broll
    candidates = candidates[:8]

    # Print the top sources in order so the log shows ranking
    if candidates:
        ranking_str = ", ".join(f"{c.get('source', 'Unknown')} (score: {c.get('_score', 0.0):.1f})" for c in candidates)
        print(f"[B-roll] Top candidates after scoring: {ranking_str}")

    # Run Gemini Vision matching on candidates
    if candidates:
        print(f"[B-roll] Segment {segment_index}: Ranking {len(candidates)} candidates from: {', '.join(set(c.get('source', 'Unknown') for c in candidates))}…")
        thumbs = []
        valid_candidates = []
        for idx, cand in enumerate(candidates):
            if budget_exceeded():
                break
            try:
                r_thumb = requests.get(cand["thumb_url"], timeout=15)
                r_thumb.raise_for_status()
                from PIL import Image
                import io
                Image.open(io.BytesIO(r_thumb.content)).verify()
                
                thumbs.append(r_thumb.content)
                valid_candidates.append(cand)
            except Exception as e:
                print(f"[B-roll] Failed/invalid thumbnail {idx} from {cand.get('source', 'Unknown')}: {e}")

        if valid_candidates:
            print(f"[B-roll] Segment {segment_index}: Ranking {len(valid_candidates)} candidates from: {', '.join(set(c.get('source', 'Unknown') for c in valid_candidates))}…")
            from pipeline.vision_match import vision_rank_broll
            best_idx, match_found = vision_rank_broll(thumbs, narration, query)

            # Sort valid_candidates so best_idx is first, followed by remaining candidates
            candidate_order = []
            if match_found and best_idx is not None and best_idx < len(valid_candidates):
                candidate_order.append(valid_candidates[best_idx])
                candidate_order.extend([c for i, c in enumerate(valid_candidates) if i != best_idx])
            else:
                candidate_order = valid_candidates

            for chosen in candidate_order:
                print(f"[B-roll] Attempting download for source: {chosen.get('source', 'Unknown')} ({chosen['video_url'][:50]}...)")
                temp_video_path = f"output/temp_video_{segment_index}.mp4"
                if _download_video_robust(chosen["video_url"], temp_video_path, segment_index):
                    if used_urls is not None:
                        used_urls.add(chosen["video_url"])
                    print(f"[B-roll] Video downloaded. Running Hyperframes overlays...")
                    _image_to_ken_burns_video(temp_video_path, out_path, w, h, duration, niche=channel, caption="")
                    if os.path.exists(temp_video_path):
                        try:
                            os.remove(temp_video_path)
                        except Exception:
                            pass
                    return out_path
                else:
                    print(f"[B-roll] Video download failed for {chosen['video_url'][:50]}, trying next candidate...")

    # ── Fallback 1: Single Frame fallback search on other videos waterfall ─────────────────
    print(f"[B-roll] Segment {segment_index}: falling back to parallel waterfall search...")
    
    # We prioritize YouTube CC and archive databases at the top of the waterfall
    other_videos = [
        ("YouTube CC (main)", lambda: _youtube_candidates(query, n=1)[0]["video_url"] if _youtube_candidates(query, n=1) else None),
        ("YouTube CC (fallback)", lambda: _youtube_candidates(clean_fallback, n=1)[0]["video_url"] if _youtube_candidates(clean_fallback, n=1) else None),
        ("YouTube CC (general)", lambda: _youtube_candidates(general_fallback, n=1)[0]["video_url"] if _youtube_candidates(general_fallback, n=1) else None),
    ]
    if NASA_BROLL_ENABLED:
        other_videos.extend([
            ("NASA video (main)", lambda: _nasa_video(query)),
            ("NASA video (fallback)", lambda: _nasa_video(clean_fallback)),
            ("NASA video (general)", lambda: _nasa_video(general_fallback)),
        ])
    other_videos.extend([
        ("DVIDS video (main)", lambda: _dvids_video(query)),
        ("DVIDS video (fallback)", lambda: _dvids_video(clean_fallback)),
        ("DVIDS video (general)", lambda: _dvids_video(general_fallback)),
        ("Wikimedia video (main)", lambda: _wikimedia_video(query)),
        ("Wikimedia video (fallback)", lambda: _wikimedia_video(clean_fallback)),
        ("Wikimedia video (general)", lambda: _wikimedia_video(general_fallback)),
        ("Archive video (main)", lambda: _archive_video(query)),
        ("Archive video (fallback)", lambda: _archive_video(clean_fallback)),
        ("Archive video (general)", lambda: _archive_video(general_fallback)),
    ])
    
    # Stock sites are fallbacks at the bottom of the waterfall list
    other_videos.extend([
        ("Pixabay (main)", lambda: _pixabay_video(query)),
        ("Pixabay (fallback)", lambda: _pixabay_video(clean_fallback)),
        ("Pixabay (general)", lambda: _pixabay_video(general_fallback)),
        ("Coverr (main)", lambda: _coverr_video(query)),
        ("Coverr (fallback)", lambda: _coverr_video(clean_fallback)),
        ("Coverr (general)", lambda: _coverr_video(general_fallback)),
        ("Klipy GIF (main)", lambda: _klipy_video(query)),
        ("Klipy GIF (fallback)", lambda: _klipy_video(clean_fallback)),
        ("Klipy GIF (general)", lambda: _klipy_video(general_fallback)),
    ])

    # Gather candidate URLs to download in parallel (up to 5)
    candidates_to_download = []
    seen_urls = set()
    for label, fetch_url_fn in other_videos:
        if budget_exceeded():
            break
        try:
            video_url = fetch_url_fn()
            if video_url and video_url not in seen_urls:
                if used_urls and video_url in used_urls:
                    continue
                seen_urls.add(video_url)
                candidates_to_download.append({
                    "label": label,
                    "video_url": video_url
                })
                if len(candidates_to_download) >= 5:
                    break
        except Exception as e:
            print(f"[B-roll] Failed to fetch URL for {label}: {e}")

def _has_baked_text_ocr(frame_path: str) -> bool:
    """Uses Tesseract OCR to detect hardcoded subtitle banners or text lines on candidate video frame top/bottom strips."""
    if not frame_path or not os.path.exists(frame_path):
        return False
    try:
        import cv2, subprocess, re, tempfile
        img = cv2.imread(frame_path)
        if img is None:
            return False
        h, w = img.shape[:2]
        
        # If it is a multi-frame collage (w > h * 2), split into individual frame images
        frames = []
        if w > h * 2:
            fw = w // 3
            frames = [img[:, :fw], img[:, fw:fw*2], img[:, fw*2:]]
        else:
            frames = [img]
            
        for f in frames:
            fh, fw = f.shape[:2]
            top_crop = f[:int(fh * 0.25), :]
            mid_crop = f[int(fh * 0.25):int(fh * 0.70), :]
            bot_crop = f[int(fh * 0.70):, :]
            
            # 1) Check for watermark/disclaimer keywords anywhere on full frame
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_full:
                tmp_full_path = tmp_full.name
            cv2.imwrite(tmp_full_path, f)
            try:
                cmd = ['tesseract', tmp_full_path, 'stdout', '--oem', '1', '--psm', '11', '-l', 'eng']
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                full_text = res.stdout.lower()
                watermark_words = [
                    "stocksubmitter", "shutterstock", "watermark", "depositphotos", "dreamstime",
                    "gettyimages", "videohive", "pond5", "envato", "rights reserved", "all rights",
                    "copyright", "subscribe", "no copyright", "stock footage", "preview",
                    "recommendatory", "disclaimer", "investment", "subject to", "terms",
                    "upstox", "paytm", "zerodha", "groww", "download", "crystal maze"
                ]
                if any(wm in full_text for wm in watermark_words):
                    if os.path.exists(tmp_full_path):
                        os.remove(tmp_full_path)
                    return True
            except Exception:
                pass
            if os.path.exists(tmp_full_path):
                os.remove(tmp_full_path)

            # 2) Check top and bottom strips for multi-word subtitles and disclaimers
            disclaimer_words = {"recommendatory", "disclaimer", "copyright", "reserved", "investment", "upstox", "paytm", "zerodha", "groww", "subscribe", "terms", "condition"}
            for crop in (top_crop, bot_crop):
                if crop.size == 0:
                    continue
                # Create both raw crop and binary high-contrast crop
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                
                for img_to_ocr in (crop, thresh):
                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                        tmp_path = tmp.name
                    cv2.imwrite(tmp_path, img_to_ocr)
                    try:
                        for psm in ('11', '6'):
                            cmd = ['tesseract', tmp_path, 'stdout', '--oem', '1', '--psm', psm, '-l', 'eng']
                            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                            text = res.stdout.strip().lower()
                            words = re.findall(r'\b[a-z]{3,}\b', text)
                            if any(w in disclaimer_words for w in words):
                                if os.path.exists(tmp_path):
                                    os.remove(tmp_path)
                                return True
                            if len(words) >= 2:
                                if os.path.exists(tmp_path):
                                    os.remove(tmp_path)
                                return True
                    except Exception:
                        pass
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        return False
    except Exception:
        return False

    # Helper function for parallel downloads and frame extraction
    def download_and_extract_frame(cand, idx):
        lbl = cand["label"]
        vurl = cand["video_url"]
        temp_v = f"output/temp_video_{segment_index}_{idx}.mp4"
        temp_f = f"output/temp_frame_{segment_index}_{idx}.jpg"
        
        for p in [temp_v, temp_f]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        
        print(f"[B-roll] Downloading video from {lbl} in parallel...")
        if _download_video_robust(vurl, temp_v, f"{segment_index}_{idx}"):
            if _extract_collage_to_file(temp_v, temp_f):
                if _has_baked_text_ocr(temp_f):
                    print(f"[B-roll] Skipping candidate '{lbl}' due to detected baked text overlay/subtitles.")
                else:
                    with open(temp_f, "rb") as fh:
                        f_data = fh.read()
                    return {
                        "label": lbl,
                        "video_url": vurl,
                        "temp_v": temp_v,
                        "temp_f": temp_f,
                        "frame_data": f_data
                    }
        
        # Cleanup on failure
        for p in [temp_v, temp_f]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        return None

    # Download candidates in parallel threads
    import concurrent.futures
    downloaded_results = []
    if candidates_to_download:
        max_workers = min(len(candidates_to_download), 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(download_and_extract_frame, cand, i)
                for i, cand in enumerate(candidates_to_download)
            ]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    res = fut.result()
                    if res:
                        downloaded_results.append(res)
                except Exception as e:
                    print(f"[B-roll] Thread download failed: {e}")

    # Rank downloaded candidates using Gemini Vision Match in one batch
    from pipeline.vision_match import vision_rank_broll
    if downloaded_results:
        print(f"[B-roll] Segment {segment_index}: Ranking {len(downloaded_results)} downloaded candidates in batch...")
        thumbs = [r["frame_data"] for r in downloaded_results]
        best_idx, match_found = vision_rank_broll(thumbs, narration, query)
        
        winner = None
        if match_found and best_idx is not None and 0 <= best_idx < len(downloaded_results):
            winner = downloaded_results[best_idx]
            winner_idx = best_idx
            print(f"[B-roll] Parallel winner chosen! Source: {winner['label']} (Index: {best_idx})")
        else:
            winner = downloaded_results[0]
            winner_idx = 0
            print(f"[B-roll] Fallback video candidate chosen! Source: {winner['label']} (Index: 0)")
        
        # Run the video through Hyperframes overlays
        print(f"[B-roll] Winner video. Running Hyperframes overlays...")
        _image_to_ken_burns_video(winner["temp_v"], out_path, w, h, duration, niche=channel, caption="")
        
        # Copy winner credit metadata if present
        winner_credit_file = f"output/broll_{segment_index}_{winner_idx}_credit.json"
        target_credit_file = f"output/broll_{segment_index}_credit.json"
        if os.path.exists(winner_credit_file):
            import shutil
            shutil.copy(winner_credit_file, target_credit_file)

        if used_urls is not None:
            used_urls.add(winner["video_url"])
            
        # Clean up all files
        for r in downloaded_results:
            for p in [r["temp_v"], r["temp_f"]]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        return out_path

    # ── Fallback 2: image sources (all converted with Ken Burns) ─────────────────────
    print(f"[B-roll] Segment {segment_index}: trying image sources…")

    img_sources = []
    if NASA_BROLL_ENABLED:
        img_sources.extend([
            (_nasa_image, query),
            (_nasa_image, clean_fallback),
        ])
    img_sources.extend([
        (_openverse_image, query),
        (_openverse_image, clean_fallback),
        (_wikipedia_image, query),
        (_wikipedia_image, clean_fallback)
    ])

    img_url = None
    for img_fn, q in img_sources:
        candidate_img = img_fn(q)
        if candidate_img and (used_urls is None or candidate_img not in used_urls):
            img_url = candidate_img
            if used_urls is not None:
                used_urls.add(img_url)
            break

    if img_url:
        try:
            r = requests.get(img_url, timeout=30, headers={"User-Agent": "yt-auto/1.0"})
            r.raise_for_status()
            with open(img_path, "wb") as f:
                f.write(r.content)
            print(f"[B-roll] Segment {segment_index}: image downloaded. Applying Ken Burns…")
            _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
            return out_path
        except Exception as e:
            print(f"[B-roll] Image source failed: {e}. Trying Pollinations…")

    # ── Fallback 3: Pollinations AI / Unsplash 4K image ─────────────────────────────
    if _pollinations_image(query, w, h, img_path):
        print(f"[B-roll] Segment {segment_index}: Stock image OK. Applying Ken Burns motion…")
        _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
        return out_path

    # ── Fallback 4: Dynamic Cinematic Particle / Bokeh Motion Generator ─────────────
    print(f"[B-roll] Segment {segment_index}: Generating cinematic procedural particle motion...")
    cmd_procedural = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"mandelbrot=s={w}x{h}:r=30:maxiter=120",
        "-t", f"{duration:.3f}",
        "-vf", "eq=contrast=1.15:saturation=1.4:gamma=0.9,hue=s=1:h=t*25,unsharp=5:5:0.8:5:5:0.4,setsar=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path
    ]
    subprocess.run(cmd_procedural, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path
    _image_to_ken_burns_video(img_path, out_path, w, h, duration, niche=channel, caption="")
    return out_path
