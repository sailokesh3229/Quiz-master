"""The runtime encoder must preserve the bank/grading vector contract."""
import sys
import numpy as np

from app.embeddings import MiniLMEncoder


def test_runtime_encoder_shapes_and_normalization(embed_model):
    texts = ["A chemical equation describes a reaction.", "Plants use sunlight for photosynthesis."]
    batch = embed_model.encode(texts)
    assert batch.shape == (2, 384)
    assert batch.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(batch, axis=1), [1, 1], atol=1e-6)
    np.testing.assert_allclose(embed_model.encode(texts[0]), batch[0], atol=1e-5)
    assert embed_model.encode([]).shape == (0, 384)


def test_runtime_truncates_long_text_without_loading_torch(embed_model):
    result = embed_model.encode("chemical reaction " * 1000)
    assert result.shape == (384,)
    assert np.isfinite(result).all()
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules


def test_missing_model_fails_without_download(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="Embedding model is missing"):
        MiniLMEncoder(tmp_path)
