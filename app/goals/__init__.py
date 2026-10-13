"""Public API for the goal package.

Deliberately small: everything downstream (state, planner, executor) only ever
needs the ``Goal`` object and the ``GoalExtractor`` that produces it.
"""
from .goal import Goal
from .extractor import GoalExtractor

__all__ = ["Goal", "GoalExtractor"]