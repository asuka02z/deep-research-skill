#!/bin/bash
rsync -av --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='sync.sh' \
  /Users/zhangshanbin/Documents/research/.cursor/skills/deep-research-python/ \
  /Users/zhangshanbin/Documents/research/skills/deep-research-python/
echo "Synced. Run 'git diff' to review changes."
