#!/bin/zsh
cd "${0:A:h}"
PY="${XIN_PYTHON:-}"
BUNDLED="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -z "$PY" && -x "$BUNDLED" ]]; then PY="$BUNDLED"; fi
if [[ -z "$PY" ]]; then PY="$(command -v python3)"; fi
if [[ -z "$PY" ]]; then echo '需要 Python 3.11+，请先安装后再启动。'; read '?按回车结束'; exit 1; fi
"$PY" launcher.py
if [[ $? -ne 0 ]]; then read '?启动失败，按回车保留错误信息后结束'; fi
