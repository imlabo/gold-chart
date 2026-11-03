#!/usr/bin/env bash
# GitHub Actions の定期実行は数時間遅れることがあるため、1回のジョブの中で5分ごとに取得と公開を繰り返す。
# 1ジョブの上限は6時間なので 5時間50分で抜け、cron（3時間ごと）が次のジョブを起こす。
set -u
ITER="${LOOP_ITER:-70}"        # 70回 × 5分 = 5時間50分
SLEEP="${LOOP_SLEEP:-300}"
REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
for i in $(seq 1 "$ITER"); do
  start=$(date +%s)
  echo "=== iteration $i/$ITER $(date -u +%FT%TZ) ==="
  rm -rf out
  if python3 scripts/fetch_yahoo.py out; then
    ( cd out \
      && git init -q -b data \
      && git config user.name "market-data-bot" \
      && git config user.email "actions@users.noreply.github.com" \
      && git add -A \
      && git commit -qm "data $(date -u +%FT%TZ)" \
      && git push -qf "$REPO_URL" data ) || echo "push failed (will retry next iteration)"
  else
    echo "fetch failed entirely; keeping previous data"
  fi
  [ "$i" -ge "$ITER" ] && break
  elapsed=$(( $(date +%s) - start ))
  wait=$(( SLEEP - elapsed )); [ "$wait" -gt 0 ] && sleep "$wait"
done
echo "loop finished"
