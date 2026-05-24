"""Source adapters. Each yields `Candidate` objects to feed the pipeline."""

from .base import Candidate, Source, get_adapter

__all__ = ["Candidate", "Source", "get_adapter"]
