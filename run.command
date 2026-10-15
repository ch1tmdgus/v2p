#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
trap 'echo; echo "실행이 중단되었습니다. 위 오류를 확인하세요."; read -r -p "Enter를 누르면 닫힙니다."' ERR
if ! command -v python3 >/dev/null; then
  echo "Python 3.11 이상을 먼저 설치하세요: https://www.python.org/downloads/macos/"
  exit 1
fi
if ! command -v tesseract >/dev/null; then
  echo "Tesseract가 필요합니다. 터미널에서 brew install tesseract 를 실행하세요."
  read -r -p "Enter를 누르면 닫힙니다."
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export PORT="${PORT:-5050}"
(sleep 2; open "http://127.0.0.1:$PORT") &
echo "종료하려면 이 창에서 Control+C를 누르세요."
exec .venv/bin/python app.py
