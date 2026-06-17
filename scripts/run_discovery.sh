#!/usr/bin/env bash

OUT_DIR="discover_out"
mkdir -p "$OUT_DIR"

SITES=(
  "svt.se"
  "dn.se"
  "svd.se"
  "aftonbladet.se"
  "expressen.se"
  "gp.se"
  "sydsvenskan.se"
  "di.se"
  "hd.se"
  "vk.se"
  "nt.se"
  "unt.se"
)

VERIFY=""
if [ "${1:-}" = "--verify" ]; then
    VERIFY="--verify"
fi

echo "===== Sitemap Discovery Report ====="
printf "%-25s %-10s\n" "Site" "Status"
printf "%s\n" "------------------------------------------"
PASS=0
FAIL=0

for site in "${SITES[@]}"; do
    outfile="$OUT_DIR/${site%.*}.txt"
    python3 -m tools.discover_sitemaps "$site" $VERIFY > "$outfile" 2>&1 &
    pid=$!
    slept=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        slept=$((slept + 1))
        if [ "$slept" -ge 30 ]; then
            kill "$pid" 2>/dev/null || true
            echo "TIMEOUT (30s)" >> "$outfile"
            break
        fi
    done
    wait "$pid" 2>/dev/null
    rc=$?
    if [ "$slept" -ge 30 ]; then
        STATUS="FAIL"
        ((FAIL++))
    elif [ "$rc" -eq 0 ]; then
        STATUS="PASS"
        ((PASS++))
    else
        STATUS="FAIL"
        ((FAIL++))
    fi
    printf "%-25s %-10s\n" "$site" "$STATUS"
done

printf "%s\n" "------------------------------------------"
echo "PASS: $PASS  FAIL: $FAIL  Total: ${#SITES[@]}"
echo ""

echo "Check individual results:"
for site in "${SITES[@]}"; do
    outfile="$OUT_DIR/${site%.*}.txt"
    echo "  cat $outfile"
done
