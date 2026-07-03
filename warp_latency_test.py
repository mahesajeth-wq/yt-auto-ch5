import sys
import time
import requests

WARP = {
    'http':  'socks5://127.0.0.1:40000',
    'https': 'socks5://127.0.0.1:40000',
}

ENDPOINTS = {
    'YouTube':     'https://www.googleapis.com/upload/youtube/v3/videos',
    'Dailymotion': 'https://api.dailymotion.com/',
    'Rumble':      'https://web10.rumble.com',
}

def probe(url, proxies=None, label=''):
    try:
        t = time.time()
        r = requests.get(url, proxies=proxies, timeout=12,
                         allow_redirects=True, stream=True)
        r.close()
        return r.status_code, int((time.time() - t) * 1000)
    except Exception as e:
        print(f'  [{label}] ERROR: {e}')
        return 'FAIL', -1

results = []
for name, url in ENDPOINTS.items():
    d_status, d_ms = probe(url, proxies=None, label=f'{name} direct')
    w_status, w_ms = probe(url, proxies=WARP,  label=f'{name} WARP')
    if d_ms > 0 and w_ms > 0:
        delta = f'+{w_ms - d_ms}ms'
    else:
        delta = 'N/A'
    results.append((name, d_status, d_ms, w_status, w_ms, delta))

print()
print(f"{'Platform':<13} | {'Direct':^6} | {'Direct ms':^10} | {'WARP':^6} | {'WARP ms':^8} | {'Overhead':^9}")
print('-' * 70)
for name, ds, dm, ws, wm, delta in results:
    dm_str = f'{dm}ms' if dm > 0 else 'FAIL'
    wm_str = f'{wm}ms' if wm > 0 else 'FAIL'
    print(f"{name:<13} | {str(ds):^6} | {dm_str:^10} | {str(ws):^6} | {wm_str:^8} | {delta:^9}")

valid = [(r[4] - r[2]) for r in results if r[2] > 0 and r[4] > 0]
if valid:
    avg = sum(valid) // len(valid)
    print(f'\nWARP overhead: avg +{avg}ms across {len(valid)} endpoint(s)')
else:
    print('\nWARP overhead: could not calculate — one or more requests failed')

all_ok = all(isinstance(r[3], int) and r[3] < 500 for r in results)
verdict = 'ALL WARP PATHS CLEAR — ready to integrate' if all_ok else 'ONE OR MORE WARP PATHS FAILED — do not integrate yet'
print(f'\nResult: {verdict}')
sys.exit(0 if all_ok else 1)
