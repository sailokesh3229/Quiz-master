#!/usr/bin/env bash
# Render build step. Installs CPU-only torch first (the default PyPI torch
# pulls ~2GB of CUDA libs Render can't use), then the rest, then fetches the
# Chroma store, which is too large for git, from CHROMA_ZIP_URL.
set -euo pipefail

pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

CHROMA_DIR="${CHROMA_DB_PATH:-../ingestion/chroma_db}"
if [ -n "${CHROMA_ZIP_URL:-}" ]; then
  echo "Downloading Chroma store..."
  curl -fL "$CHROMA_ZIP_URL" -o /tmp/chroma_db.zip
  rm -rf "$CHROMA_DIR"
  mkdir -p "$CHROMA_DIR"
  python -m zipfile -e /tmp/chroma_db.zip "$CHROMA_DIR"
  rm /tmp/chroma_db.zip
else
  echo "WARNING: CHROMA_ZIP_URL not set; no textbook content will be available."
fi

# Pre-download the embedding model so the first request isn't slow.
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
