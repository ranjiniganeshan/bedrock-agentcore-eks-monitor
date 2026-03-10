import os
import sys
import time
import json
import threading

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

BOOT_TIME = time.time()
READY = False

def log(msg: str):
    print(msg, flush=True)

def must_get_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or v == "":
        raise RuntimeError(f"missing required env {name}")
    return v

def start_background_ready_timer():
    global READY
    startup_delay_s = int(os.getenv("STARTUP_DELAY_SECONDS", "0"))
    def worker():
        global READY
        if startup_delay_s > 0:
            log(f"[startup] sleeping {startup_delay_s}s before becoming ready")
            time.sleep(startup_delay_s)
        READY = True
        log("[startup] READY=true")
    t = threading.Thread(target=worker, daemon=True)
    t.start()

def maybe_crash_on_start():
    """
    Simulate CrashLoopBackOff due to bad config / missing env / panic.
    """
    # 1) Missing required env
    if os.getenv("REQUIRE_ENV", "0") == "1":
        must_get_env("VAR_X")

    # 2) Missing config file
    if os.getenv("REQUIRE_CONFIG", "0") == "1":
        cfg_path = os.getenv("CONFIG_PATH", "/config/config.json")
        if not os.path.exists(cfg_path):
            raise RuntimeError(f"failed to load config file: {cfg_path} does not exist")
        # Optionally validate JSON
        with open(cfg_path, "r") as f:
            json.load(f)

    # 3) Explicit crash mode
    fail_mode = os.getenv("FAIL_MODE", "").lower()
    if fail_mode in ("panic", "crash", "exit1"):
        raise RuntimeError("panic: simulated crash requested (FAIL_MODE)")

def maybe_start_memory_hog():
    """
    Simulate OOMKilled by allocating real unique memory.
    """
    mb = int(os.getenv("MEMORY_HOG_MB", "0"))
    if mb <= 0:
        return

    log(f"[oom] starting memory hog: targeting ~{mb} MB")

    buf = []
    for i in range(mb):
        # Allocate a NEW 1MB object each loop (unique memory)
        buf.append(bytearray(1024 * 1024))

        if i % 10 == 0 and i > 0:
            log(f"[oom] allocated ~{i} MB so far")
            time.sleep(0.02)

    log("[oom] done allocating; holding memory to trigger limit")
    # Keep reference alive
    while True:
        time.sleep(1)

@app.get("/healthz")
def healthz():
    return jsonify(ok=True, uptime_seconds=int(time.time() - BOOT_TIME))

@app.get("/readyz")
def readyz():
    # Simulate readiness probe failures
    if os.getenv("FORCE_NOT_READY", "0") == "1":
        return jsonify(ready=False, reason="FORCE_NOT_READY=1"), 503

    # Optional dependency gate (fake)
    dep = os.getenv("DEPENDENCY_REQUIRED", "0")
    dep_ok = os.getenv("DEPENDENCY_OK", "1")
    if dep == "1" and dep_ok != "1":
        return jsonify(ready=False, reason="dependency check failed"), 503

    if not READY:
        return jsonify(ready=False, reason="startup delay"), 503

    return jsonify(ready=True)

def format_uptime(seconds: int) -> str:
    """Return human-readable uptime e.g. '2m 15s' or '1h 5m'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if m or s:
        return f"{h}h {m}m {s}s" if s else f"{h}h {m}m"
    return f"{h}h"

@app.get("/")
def root():
    uptime_seconds = int(time.time() - BOOT_TIME)
    env = {
        "FAIL_MODE": os.getenv("FAIL_MODE", ""),
        "REQUIRE_ENV": os.getenv("REQUIRE_ENV", "0"),
        "REQUIRE_CONFIG": os.getenv("REQUIRE_CONFIG", "0"),
        "CONFIG_PATH": os.getenv("CONFIG_PATH", "/config/config.json"),
        "STARTUP_DELAY_SECONDS": os.getenv("STARTUP_DELAY_SECONDS", "0"),
        "FORCE_NOT_READY": os.getenv("FORCE_NOT_READY", "0"),
        "MEMORY_HOG_MB": os.getenv("MEMORY_HOG_MB", "0"),
    }
    if request.args.get("json") == "1":
        return jsonify(
            message="agentic-devops-demo-app",
            uptime_seconds=uptime_seconds,
            env=env,
        )
    return render_template(
        "index.html",
        title="Agentic DevOps Demo App",
        uptime_human=format_uptime(uptime_seconds),
        env=env,
    )

def main():
    port = int(os.getenv("PORT", "8080"))

    # Crash-on-start scenarios
    try:
        maybe_crash_on_start()
    except Exception as e:
        log(str(e))
        sys.exit(1)

    # Readiness timer
    start_background_ready_timer()

    # Optional memory hog (for OOMKilled)
    try:
        maybe_start_memory_hog()
    except MemoryError:
        log("[oom] Python MemoryError before cgroup kill (still ok for demo)")
        # keep running so you can see behavior
        pass

    log(f"[startup] listening on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
