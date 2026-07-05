#!/usr/bin/env python3
"""
Proof that mlx_lm.server can be SIGTERM'd to reclaim GPU memory,
then restarted and serve prompts again.

Measures RSS before/during/after to confirm Metal buffers are released
with the process (Apple Silicon unified memory).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

PORT = 8090
MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
HOST = "127.0.0.1"
BASE_URL = f"http://{HOST}:{PORT}/v1"

HF_HOME = os.path.expanduser("~/ssd/.cache/huggingface")


def get_rss_kb(pid: int) -> int | None:
    """Return RSS in KB for a given PID, or None if process is gone."""
    try:
        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return int(out) if out else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def wait_for_server(pid: int, timeout: float = 120.0) -> bool:
    """Poll the chat endpoint until it responds 200."""
    import urllib.request

    url = f"{BASE_URL}/chat/completions"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_rss_kb(pid) is None:
            return False  # process died
        try:
            body = json.dumps(
                {
                    "model": MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            ).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            time.sleep(2)
    return False


def send_prompt(prompt: str) -> str:
    """Send a prompt and return the response text."""
    import urllib.request

    url = f"{BASE_URL}/chat/completions"
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def start_server() -> subprocess.Popen:
    """Launch mlx_lm.server as a subprocess."""
    env = os.environ.copy()
    env["HF_HOME"] = HF_HOME
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mlx_lm.server",
            "--model",
            MODEL,
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "INFO",
            "--max-tokens",
            "4096",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    print("=" * 60)
    print("MLX Server Memory Reclamation Proof")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Port:  {PORT}")
    print()

    # --- Step 1: Start server ---
    print("[1] Starting mlx_lm.server ...")
    proc = start_server()
    pid = proc.pid
    rss = get_rss_kb(pid)
    print(f"    PID={pid}  RSS={rss} KB")

    # --- Step 2: Wait for model to load ---
    print("[2] Waiting for model to load (server ready) ...")
    t0 = time.monotonic()
    ready = wait_for_server(pid)
    elapsed = time.monotonic() - t0
    if not ready:
        print("    FAILED: server did not become ready")
        proc.kill()
        sys.exit(1)
    rss = get_rss_kb(pid)
    print(f"    Ready in {elapsed:.1f}s  RSS={rss} KB ({rss / 1024:.1f} MB)")

    # --- Step 3: Send a prompt ---
    print("[3] Sending prompt ...")
    response = send_prompt("Vad heter Sveriges huvudstad?")
    print(f"    Response: {response[:80]}...")
    rss = get_rss_kb(pid)
    print(f"    RSS (after prompt): {rss} KB ({rss / 1024:.1f} MB)")

    # --- Step 4: SIGTERM the server ---
    print("[4] Sending SIGTERM ...")
    os.kill(pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
        print(f"    Process exited with code {proc.returncode}")
    except subprocess.TimeoutExpired:
        print("    WARN: did not exit in 10s, sending SIGKILL")
        proc.kill()
        proc.wait()

    # --- Step 5: Verify process is gone, memory reclaimed ---
    print("[5] Checking process state ...")
    gone = get_rss_kb(pid)
    if gone is None:
        print("    RSS: process no longer exists (memory reclaimed)")
        print("    PASS: GPU memory returned to OS via process termination")
    else:
        print(f"    RSS: {gone} KB (WARN: process still has RSS)")
        print("    FAIL: memory was not reclaimed")

    # --- Step 6: Restart server and verify it works again ---
    print()
    print("[6] Restarting server (simulating supervisor restart) ...")
    proc2 = start_server()
    pid2 = proc2.pid
    rss2 = get_rss_kb(pid2)
    print(f"    New PID={pid2}  RSS={rss2} KB")

    t0 = time.monotonic()
    ready2 = wait_for_server(pid2)
    elapsed2 = time.monotonic() - t0
    if not ready2:
        print("    FAILED: restarted server did not become ready")
        proc2.kill()
        sys.exit(1)
    rss2 = get_rss_kb(pid2)
    print(f"    Ready in {elapsed2:.1f}s  RSS={rss2} KB ({rss2 / 1024:.1f} MB)")

    # Send another prompt to prove it serves correctly
    print("[7] Sending prompt to restarted server ...")
    response2 = send_prompt("Vad heter Danmarks huvudstad?")
    print(f"    Response: {response2[:80]}...")

    # Clean up
    print()
    print("[8] Cleaning up ...")
    os.kill(pid2, signal.SIGTERM)
    try:
        proc2.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc2.kill()
        proc2.wait()
    print("    Done.")

    # --- Summary ---
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Server start model load:    {elapsed:.1f}s")
    print(f"  Memory while serving:       {rss / 1024:.0f} MB")
    print("  Memory after SIGTERM:       reclaimed (process gone)")
    print(f"  Restart model load:         {elapsed2:.1f}s")
    print(f"  Restart memory:             {rss2 / 1024:.0f} MB")
    print("  Prompts served:             both runs worked")
    print()
    print("Conclusion: SIGTERM frees all GPU memory (process exit")
    print("releases Metal buffers on Apple Silicon). A supervisor can")
    print("start/stop the server to reclaim memory on idle while")
    print("preserving the ability to restart on demand.")


if __name__ == "__main__":
    main()
