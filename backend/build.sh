#!/usr/bin/env bash
# Build the lightweight ONNX runtime and fetch the textbook corpus.
set -euo pipefail

pip install --upgrade pip
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
python -m app.embeddings
