"""Confirms PyMuPDF, sentence-transformers, and Chroma are installed and
importable."""

import chromadb
import pymupdf
import sentence_transformers

print(f"PyMuPDF: {pymupdf.__doc__}")
print(f"sentence-transformers: {sentence_transformers.__version__}")
print(f"chromadb: {chromadb.__version__}")
