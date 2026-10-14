#!/bin/bash
# Double-click or run from Terminal. Excel is written to reports/live.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/rank_qiandai.py"

if [[ ! -f "$PY" ]]; then
  printf '找不到 %s\n' "$PY"
  exit 1
fi

if [[ $# -eq 0 ]]; then
  printf '请输入收盘清单文件名（位于 reports/live）: '
  IFS= read -r name
  if [[ -z "${name}" ]]; then
    printf '没有输入文件名\n'
    exit 1
  fi
  python3 "$PY" "$name"
  printf '\n按回车关闭\n'
  IFS= read -r _
else
  exec python3 "$PY" "$@"
fi
