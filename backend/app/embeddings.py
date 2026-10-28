"""CPU MiniLM inference without importing PyTorch or transformers.

Chroma distributes an ONNX export of the same all-MiniLM-L6-v2 model
used during ingestion. Keep its 256-token limit, attention-weighted mean
pooling and normalized 384-dimensional output.
"""
from pathlib import Path
import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / ".models" / "minilm-onnx"


def prepare_model() -> None:
    # Chroma verifies the downloaded archive's SHA256 before extraction.
    # Run only during build, never on a student request.
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    downloader = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
    downloader.DOWNLOAD_PATH = MODEL_DIR
    downloader._download_model_if_not_exists()


class MiniLMEncoder:
    def __init__(self, model_dir: Path = MODEL_DIR):
        from tokenizers import Tokenizer
        import onnxruntime as ort

        directory = model_dir / "onnx"
        if not (directory / "model.onnx").is_file():
            raise RuntimeError("Embedding model is missing; run python -m app.embeddings during build")
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=256)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        self.session = ort.InferenceSession(str(directory / "model.onnx"),
                                           sess_options=options, providers=["CPUExecutionProvider"])

    def encode(self, texts: str | list[str]) -> np.ndarray:
        single = isinstance(texts, str)
        documents = [texts] if single else list(texts)
        if not documents:
            return np.empty((0, 384), dtype=np.float32)
        batches = []
        # Small batches limit peak memory on the free Render instance.
        for start in range(0, len(documents), 4):
            tokens = self.tokenizer.encode_batch(documents[start:start + 4])
            ids = np.asarray([t.ids for t in tokens], dtype=np.int64)
            mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
            hidden = self.session.run(None, {"input_ids": ids, "attention_mask": mask,
                                             "token_type_ids": np.zeros_like(ids)})[0]
            weights = mask[..., None]
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
            normalized = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            batches.append(normalized.astype(np.float32))
        result = np.concatenate(batches)
        return result[0] if single else result


if __name__ == "__main__":
    prepare_model()
    print("MiniLM ONNX model ready for offline runtime use")
