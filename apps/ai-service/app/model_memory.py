"""Coordinate memory use between local Hugging Face models."""

import gc
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from app.config import settings


class ModelMemoryCoordinator:
    """Keep at most one local model resident when memory is constrained."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_owner: object | None = None
        self._active_unload: Callable[[], None] | None = None

    @contextmanager
    def claim(self, owner: object, unload: Callable[[], None]) -> Iterator[None]:
        if settings.MODEL_MEMORY_MODE != "exclusive":
            yield
            return

        with self._lock:
            if self._active_owner is not owner:
                if self._active_unload is not None:
                    self._active_unload()
                    gc.collect()
                    try:
                        import torch

                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except ImportError:
                        pass
                self._active_owner = owner
                self._active_unload = unload
            yield


model_memory_coordinator = ModelMemoryCoordinator()
