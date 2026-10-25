import hashlib
import logging
from typing import Sequence
import numpy as np

logger = logging.getLogger("helix.memory.embedding_engine")

DEFAULT_DIMENSION = 384


class EmbeddingEngine:
    """Local embedding generator complying with ADR-019.
    
    Uses ONNX fastembed (bge-small-en-v1.5) locally without cloud APIs.
    Falls back to deterministic token-hashing projections if fastembed
    or model files are temporarily unavailable, ensuring high reliability.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dimension: int = DEFAULT_DIMENSION):
        self._model_name = model_name
        self._dimension = dimension
        self._fastembed_model = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        try:
            from fastembed import TextEmbedding
            self._fastembed_model = TextEmbedding(model_name=self._model_name)
            self._initialized = True
            logger.info("Fastembed ONNX TextEmbedding initialized with model: %s", self._model_name)
        except Exception as e:
            logger.warning("Could not initialize fastembed model (%s). Using fallback hash embeddings: %s", self._model_name, e)
            self._fastembed_model = None
            self._initialized = True

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string for semantic search."""
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed multiple documents or conversation chunks."""
        if not self._initialized:
            self.initialize()

        if not texts:
            return []

        if self._fastembed_model is not None:
            try:
                embeddings = list(self._fastembed_model.embed(texts))
                return [self._normalize(np.array(e, dtype=np.float32)).tolist() for e in embeddings]
            except Exception as e:
                logger.error("Fastembed embedding failed; using fallback hash embeddings: %s", e)

        # Fallback deterministic cosine embeddings
        return [self._fallback_embed(t) for t in texts]

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        if norm > 1e-12:
            return vec / norm
        return vec

    def _fallback_embed(self, text: str) -> list[float]:
        """Deterministic pseudo-semantic embedding using token hashes & n-grams."""
        vec = np.zeros(self._dimension, dtype=np.float32)
        words = text.lower().split()
        if not words:
            vec[0] = 1.0
            return vec.tolist()

        for word in words:
            # Word level feature
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % self._dimension
            sign = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += sign

            # Character n-grams for subword similarity
            if len(word) >= 3:
                for i in range(len(word) - 2):
                    tri = word[i:i+3]
                    th = int(hashlib.sha256(tri.encode("utf-8")).hexdigest(), 16)
                    vec[th % self._dimension] += 0.5 * (1.0 if ((th >> 4) & 1) else -1.0)

        norm = np.linalg.norm(vec)
        if norm > 1e-12:
            vec = vec / norm
        else:
            vec[0] = 1.0
        return vec.tolist()
