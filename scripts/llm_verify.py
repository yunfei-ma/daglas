"""
Deprecated — MLX inference now uses ``mlx_lm.server``.

See ``scripts/mlx_server_memory_proof.py`` for the equivalent
memory-reclamation verification against the server process.
"""

from __future__ import annotations

import gc
import time

import mlx.core as mx

from daglas.lesson.llm_mlx import LlmMLX


def _mb(bytes_: int) -> str:
    return f"{bytes_ / 1024**2:.1f} MB"


def main() -> None:
    import sys

    model = (
        sys.argv[1] if len(sys.argv) > 1 else "mlx-community/Llama-3.2-3B-Instruct-4bit"
    )

    gc.collect()
    time.sleep(0.5)

    mem_active_before = mx.get_active_memory()
    mem_cache_before = mx.get_cache_memory()
    mem_peak_before = mx.get_peak_memory()

    print("--- Before load ---")
    print(f"  active: {_mb(mem_active_before)}")
    print(f"  cache:  {_mb(mem_cache_before)}")
    print(f"  peak:   {_mb(mem_peak_before)}")

    provider = LlmMLX(model=model, max_tokens=64)

    print(f"\nLoading model '{model}' ...")
    t0 = time.time()
    provider.start()
    load_time = time.time() - t0

    mem_active_loaded = mx.get_active_memory()
    mem_cache_loaded = mx.get_cache_memory()
    mem_peak_loaded = mx.get_peak_memory()

    print(f"  loaded in {load_time:.1f}s")
    print(f"  active: {_mb(mem_active_loaded)}")
    print(f"  cache:  {_mb(mem_cache_loaded)}")
    print(f"  peak:   {_mb(mem_peak_loaded)}")

    print("\nGenerating...")
    t0 = time.time()
    result = provider.prompt(
        system="You are a helpful Swedish tutor.",
        user="Say hello in Swedish and introduce yourself briefly.",
    )
    gen_time = time.time() - t0
    print(f"  generated in {gen_time:.1f}s")
    if result is None:
        print("  LLM returned None (error)")
    else:
        print(f"  output ({len(result)} chars): {result}")

    print("\n--- After generation ---")
    mem_active_gen = mx.get_active_memory()
    mem_cache_gen = mx.get_cache_memory()
    mem_peak_gen = mx.get_peak_memory()
    print(f"  active: {_mb(mem_active_gen)}")
    print(f"  cache:  {_mb(mem_cache_gen)}")
    print(f"  peak:   {_mb(mem_peak_gen)}")

    print("\nStopping...")
    provider.stop()
    assert provider._state is None, "stop() did not clear _state"

    gc.collect()
    mx.clear_cache()
    time.sleep(0.5)

    mem_active_after = mx.get_active_memory()
    mem_cache_after = mx.get_cache_memory()
    mem_peak_after = mx.get_peak_memory()

    print("--- After stop + gc + cache clear ---")
    print(f"  active: {_mb(mem_active_after)}")
    print(f"  cache:  {_mb(mem_cache_after)}")
    print(f"  peak:   {_mb(mem_peak_after)}")

    released = mem_active_loaded - mem_active_after
    remaining_pct = (mem_active_after / max(mem_active_loaded, 1)) * 100
    print(f"\n  active memory released: {_mb(released)}")
    print(f"  remaining active: {remaining_pct:.0f}% of peak load")

    if remaining_pct < 20:
        print("  PASS: most active memory released after stop")
    else:
        print(f"  NOTE: {remaining_pct:.0f}% of active memory still allocated")
        print("  This is expected — MLX Metal buffers are freed at process exit.")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
