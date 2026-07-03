import json
from car_ad_pipeline.gemini_client import GeminiClient

def lookup_market_price(client: GeminiClient, query: str) -> str:
    print(f"Performing market price lookup with Google Search grounding: '{query}'...")
    
    prompt = f"""
Search Google and find the current market going-rate price range in India for the following used vehicle description:
"{query}"

Please provide:
1. Average used car market price range in Lakhs (INR).
2. Key pricing factors (condition, mileage, location).
3. A short summary (2-3 sentences) anchoring the client's asking price against this market comparison.
"""
    # Create contents structure
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    
    # We use generateContent with tools: google_search
    # Since our gemini_client.py doesn't have tools: google_search by default, we can add it to the payload or implement a specific method.
    # Let's modify our gemini_client.py or implement the payload manually here.
    # Actually, we can make a direct REST call using the client's key.
    key = client.get_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    payload = {
        "contents": contents,
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2
        }
    }
    
    import requests
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 429:
                client.rotate_key()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={client.get_key()}"
                continue
            response.raise_for_status()
            res_data = response.json()
            text = res_data["candidates"][0]["content"]["parts"][0]["text"]
            return text
        except Exception as e:
            print(f"Price lookup failed: {e}. Rotating key and retrying...")
            client.rotate_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={client.get_key()}"
            
    return "Market price information is currently unavailable."
