#!/usr/bin/env python3
import os
import sys
import time
import signal
import socket
import argparse
import subprocess
from datetime import datetime, timezone, timedelta, time as dtime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "cron_daemon.pid")
LOG_FILE = os.path.join(SCRIPT_DIR, "cron_daemon.log")
ENV_FILE = os.path.join(SCRIPT_DIR, "local_env.sh")
IST = timezone(timedelta(hours=5, minutes=30))

TARGET_TIMES = [dtime(12, 0, 0), dtime(19, 0, 0)]  # 12:00 PM & 7:00 PM IST

def log(msg):
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def load_local_env():
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                val = val.strip("\"'")
                os.environ[key] = val

def is_warp_available(host="127.0.0.1", port=40000):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

def get_next_run():
    now = datetime.now(IST)
    candidates = []
    for t in TARGET_TIMES:
        cand = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        candidates.append(cand)
    return min(candidates)

def run_job(video_format="short", publish=True):
    log(f"Job started: format={video_format}, publish={publish}")
    load_local_env()

    env = os.environ.copy()
    warp_port = 40000
    if is_warp_available("127.0.0.1", warp_port):
        proxy_str = f"socks5://127.0.0.1:{warp_port}"
        env["HTTP_PROXY"] = proxy_str
        env["HTTPS_PROXY"] = proxy_str
        env["ALL_PROXY"] = proxy_str
        log(f"WARP SOCKS5 proxy active: {proxy_str}")
    else:
        log("WARP proxy inactive on port 40000; using direct local IP")

    python_bin = sys.executable

    # Attempt 1 & Retry 2 with --resume
    for attempt in range(1, 3):
        cmd = [python_bin, "run_generate.py", "--format", video_format]
        if attempt > 1 and os.path.exists(os.path.join(SCRIPT_DIR, "output")):
            cmd.append("--resume")

        log(f"Generate attempt {attempt}/2: {' '.join(cmd)}")
        try:
            res = subprocess.run(cmd, cwd=SCRIPT_DIR, env=env)
            if res.returncode == 0:
                log("Generation success.")
                break
            log(f"Generate attempt {attempt} failed (code {res.returncode})")
        except Exception as e:
            log(f"Generate attempt {attempt} error: {e}")

        if attempt == 1:
            log("Waiting 5m cooldown before retry...")
            time.sleep(300)
    else:
        log("Generation failed all attempts.")
        return False

    if publish:
        pub_cmd = [python_bin, "run_publish.py"]
        log(f"Publishing: {' '.join(pub_cmd)}")
        try:
            res = subprocess.run(pub_cmd, cwd=SCRIPT_DIR, env=env)
            if res.returncode == 0:
                log("Publish success.")
                return True
            log(f"Publish failed (code {res.returncode})")
        except Exception as e:
            log(f"Publish error: {e}")
        return False

    return True

def daemon_loop(video_format="short", publish=True):
    log("Daemon loop active.")
    while True:
        next_run = get_next_run()
        log(f"Next scheduled slot: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        while True:
            now = datetime.now(IST)
            diff = (next_run - now).total_seconds()
            if diff <= 0:
                break
            time.sleep(min(diff, 15))
        
        try:
            run_job(video_format=video_format, publish=publish)
        except Exception as e:
            log(f"Job execution error: {e}")

def main():
    parser = argparse.ArgumentParser(description="yt-auto Local Background Daemon")
    parser.add_argument("--start", action="store_true", help="Start background daemon")
    parser.add_argument("--stop", action="store_true", help="Stop daemon")
    parser.add_argument("--status", action="store_true", help="Daemon status")
    parser.add_argument("--run-now", action="store_true", help="Immediate run once")
    parser.add_argument("--format", choices=["short", "long"], default="short")
    parser.add_argument("--publish", action="store_true", help="Publish video after generation")
    args = parser.parse_args()

    if args.stop:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped daemon PID {pid}")
            except ProcessLookupError:
                print(f"PID {pid} not running")
            os.remove(PID_FILE)
        else:
            print("No PID file found")
        return

    if args.status:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                next_run = get_next_run()
                print(f"Daemon RUNNING (PID {pid}). Next slot: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                return
            except OSError:
                print(f"PID file exists ({pid}) but process dead")
        else:
            print("Daemon NOT running")
        return

    if args.run_now:
        run_job(video_format=args.format, publish=args.publish)
        return

    if args.start:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                print(f"Daemon already running (PID {pid})")
                return
            except OSError:
                pass

        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"Fork #1 failed: {e}\n")
            sys.exit(1)

        os.setsid()
        os.umask(0)

        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"Fork #2 failed: {e}\n")
            sys.exit(1)

        sys.stdout.flush()
        sys.stderr.flush()
        
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        log(f"Daemon started PID {os.getpid()}")
        daemon_loop(video_format=args.format, publish=args.publish)

if __name__ == "__main__":
    main()
