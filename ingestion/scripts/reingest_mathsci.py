"""Resumable full re-ingestion driver for Maths + Science (task: Rule 1/2/3
audit fixes, 2026-09-02). Skips chapters already logged as done in the
progress log, so a killed/restarted run picks up where it left off."""
import sys
from pathlib import Path

from embed_and_store import embed_and_store_pdf

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = Path(__file__).resolve().parent.parent / "output" / "reingest_mathsci.log"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    LOG_PATH.parent.mkdir(exist_ok=True)
    done = set()
    if LOG_PATH.exists():
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("DONE\t"):
                done.add(line.split("\t")[1])

    tasks = []
    for cls in ["6", "7", "8", "9", "10"]:
        for subj_dir, subj in [("maths", "Maths"), ("science", "Science")]:
            base = ROOT / f"class {cls}" / subj_dir
            for pdf_path in sorted(base.glob("chapter *.pdf"), key=lambda p: int(p.stem.split()[1])):
                key = f"{cls}|{subj}|{pdf_path.name}"
                tasks.append((key, pdf_path, cls, subj))

    remaining = [t for t in tasks if t[0] not in done]
    print(f"{len(tasks)} total, {len(done)} already done, {len(remaining)} remaining")

    with LOG_PATH.open("a", encoding="utf-8") as logf:
        for key, pdf_path, cls, subj in remaining:
            try:
                result = embed_and_store_pdf(pdf_path, cls, subj, skip_tables=True)
                print(f"{key}: {result['chunks']} chunks, {result['corpus']} corpus")
                logf.write(f"DONE\t{key}\t{result['chunks']}\t{result['corpus']}\n")
                logf.flush()
            except Exception as e:
                print(f"FAILED {key}: {e}")
                logf.write(f"FAILED\t{key}\t{e}\n")
                logf.flush()


if __name__ == "__main__":
    main()
