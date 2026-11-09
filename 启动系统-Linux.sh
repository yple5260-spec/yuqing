#!/bin/sh
cd "$(dirname "$0")" || exit 1
exec "${XIN_PYTHON:-python3}" launcher.py
