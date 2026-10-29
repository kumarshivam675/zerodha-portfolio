"""Analyse: a multi-agent pipeline (research -> strategy -> risk gate) for one holding. See pipeline.py."""

from .pipeline import analyze
from .schemas import Subject

__all__ = ["Subject", "analyze"]
