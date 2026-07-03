import os
import sys
import time
import base64
import mimetypes
import requests
from car_ad_pipeline.config import GEMINI_API_BASE, GEMINI_MODEL, GEMINI_TTS_MODEL, get_api_keys

class GeminiClient:
    def __init__(self):
        self.keys = get_api_keys()
        self.current_key_idx = 0
        if not self.keys:
            raise RuntimeError("No Gemini API keys found. Configure GEMINI_API_KEY or GEMINI_API_KEYS.")
        self.uploaded_files = {}  # file_uri -> (filepath, file_name)

    def get_key(self) -> str:
        return self.keys[self.current_key_idx]

    def rotate_key(self):
        self.current_key_idx = (self.current_key_idx + 1) % len(self.keys)
        print(f"[GeminiClient] Rotated to key slot {self.current_key_idx + 1}/{len(self.keys)}")

    def _execute_with_retry(self, request_fn, payload=None, max_retries=6, retry_sleep=8):
        """
        Executes a request function (which returns a response object).
        If 429 is encountered, sleeps and retries with the SAME key.
        If it fails after max_retries, rotates the key and starts over.
        """
        for rotate_attempt in range(len(self.keys)):
            key = self.get_key()
            for attempt in range(max_retries):
                try:
                    response = request_fn(key)
                    if response.status_code == 429:
                        print(f"[GeminiClient] 429 Rate Limit encountered. Sleeping {retry_sleep}s (attempt {attempt+1}/{max_retries}) using key {key[:10]}...")
                        time.sleep(retry_sleep)
                        continue
                    if response.status_code == 403:
                        print(f"[GeminiClient] 403 Forbidden for key {key[:10]}. Key may be invalid or unauthorized. Rotating immediately.")
                        break  # break inner loop to rotate key
                    return response
                except requests.RequestException as e:
                    print(f"[GeminiClient] Request exception: {e}. Retrying same key...")
                    time.sleep(2)
            
            print(f"[GeminiClient] Key {key[:10]} exhausted or failed repeatedly. Rotating key.")
            self.rotate_key()
            if payload:
                self._reupload_payload_files(payload)
            
        raise RuntimeError("All Gemini API keys exhausted or failed.")

    def _reupload_payload_files(self, payload):
        # Find all fileUris in the payload that we have local files for
        uris_to_replace = {}
        
        def find_uris(obj):
            if isinstance(obj, dict):
                if "fileData" in obj and isinstance(obj["fileData"], dict):
                    uri = obj["fileData"].get("fileUri")
                    if uri and uri in self.uploaded_files:
                        uris_to_replace[uri] = self.uploaded_files[uri]
                else:
                    for v in obj.values():
                        find_uris(v)
            elif isinstance(obj, list):
                for item in obj:
                    find_uris(item)
                    
        find_uris(payload)
        
        if not uris_to_replace:
            return
            
        # Re-upload each file using the new key
        uri_mapping = {}
        for old_uri, (filepath, old_name) in uris_to_replace.items():
            print(f"[GeminiClient] Re-uploading {filepath} on new key to resolve authorization mismatch...")
            new_name, new_uri = self._upload_file_direct(filepath)
            if self.wait_for_file_active(new_name):
                uri_mapping[old_uri] = new_uri
                self.uploaded_files[new_uri] = (filepath, new_name)
            else:
                raise RuntimeError(f"Re-uploaded file {filepath} failed to become active.")
                
        # Update the payload with new URIs
        def replace_uris(obj):
            if isinstance(obj, dict):
                if "fileData" in obj and isinstance(obj["fileData"], dict):
                    uri = obj["fileData"].get("fileUri")
                    if uri in uri_mapping:
                        obj["fileData"]["fileUri"] = uri_mapping[uri]
                else:
                    for v in obj.values():
                        replace_uris(v)
            elif isinstance(obj, list):
                for item in obj:
                    replace_uris(item)
                    
        replace_uris(payload)

    def _upload_file_direct(self, filepath: str) -> tuple[str, str]:
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "video/mp4"
            
        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        key = self.get_key()
        url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=media&key={key}"
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        response = requests.post(url, headers=headers, data=file_bytes, timeout=300)
        response.raise_for_status()
        res_data = response.json()
        file_name = res_data["file"]["name"]
        file_uri = res_data["file"]["uri"]
        return file_name, file_uri

    def upload_file(self, filepath: str) -> str:
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "video/mp4"
            
        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        
        print(f"Uploading file '{filename}' ({file_size / (1024*1024):.2f} MB) to Gemini Files API...")
        
        def make_request(key):
            url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=media&key={key}"
            headers = {
                "Content-Type": mime_type,
                "Content-Length": str(file_size),
                "X-Goog-Upload-Header-Content-Length": str(file_size),
                "X-Goog-Upload-Header-Content-Type": mime_type,
            }
            with open(filepath, "rb") as f:
                file_bytes = f.read()
            return requests.post(url, headers=headers, data=file_bytes, timeout=300)
            
        response = self._execute_with_retry(make_request)
        response.raise_for_status()
        res_data = response.json()
        file_name = res_data["file"]["name"]
        file_uri = res_data["file"]["uri"]
        print(f"Uploaded successfully: {file_name} -> {file_uri}")
        self.uploaded_files[file_uri] = (filepath, file_name)
        return file_name, file_uri

    def wait_for_file_active(self, file_name: str, max_timeout_seconds: int = 180) -> bool:
        start_time = time.time()
        while time.time() - start_time < max_timeout_seconds:
            def make_request(key):
                url = f"{GEMINI_API_BASE}/{file_name}?key={key}"
                return requests.get(url, timeout=30)
                
            try:
                response = self._execute_with_retry(make_request, max_retries=3, retry_sleep=5)
                response.raise_for_status()
                data = response.json()
                state = data.get("state")
                if state == "ACTIVE":
                    print(f"File {file_name} is ACTIVE.")
                    return True
                elif state == "FAILED":
                    raise RuntimeError(f"Gemini File API processing failed: {data}")
                else:
                    print(f"File state is {state}. Retrying in 5 seconds...")
                    time.sleep(5)
            except Exception as e:
                print(f"[GeminiClient] Error polling file status: {e}. Retrying...")
                time.sleep(5)
        return False

    def generate_content(self, contents: list, response_schema: dict = None, low_res: bool = False, model: str = None) -> str:
        model_name = model or GEMINI_MODEL
        
        def make_request(key):
            url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={key}"
            gen_config = {
                "temperature": 0.2 if response_schema else 0.7,
            }
            if response_schema:
                gen_config["responseMimeType"] = "application/json"
                gen_config["responseSchema"] = response_schema
            payload = {
                "contents": contents,
                "generationConfig": gen_config
            }
            return requests.post(url, json=payload, timeout=180)
            
        response = self._execute_with_retry(make_request, payload=contents)
        response.raise_for_status()
        res_data = response.json()
        text = res_data["candidates"][0]["content"]["parts"][0]["text"]
        return text

    def generate_tts(self, text: str, voice: str = "Aoede", vocal_tone: str = "confident") -> bytes:
        def make_request(key):
            url = f"{GEMINI_API_BASE}/models/{GEMINI_TTS_MODEL}:generateContent?key={key}"
            director_instructions = (
                "Vocal Delivery Guide: Speak in an EXTREMELY ENERGETIC, happy, and highly enthusiastic salesman tone. "
                "Delivery must be upbeat, friendly, fast-paced but clear, filled with excitement and positive energy. "
                f"Make it sound attractive, engaging, and persuasive. Tone setting: {vocal_tone}."
            )
            full_prompt = (
                f"Director Instructions:\n{director_instructions}\n\n"
                f"Narration text to speak (Speak ONLY the following Hindi text): {text}"
            )
            payload = {
                "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                    },
                },
            }
            return requests.post(url, json=payload, timeout=120)
            
        response = self._execute_with_retry(make_request)
        response.raise_for_status()
        res_data = response.json()
        inline = res_data["candidates"][0]["content"]["parts"][0]["inlineData"]
        return base64.b64decode(inline["data"])
