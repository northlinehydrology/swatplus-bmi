"""SWAT+ through the Basic Model Interface, stepping SWAT+ one day at a time."""

from .bmi import SwatPlusBmi
from .exchange import ExchangeError

__all__ = ["SwatPlusBmi", "ExchangeError"]
__version__ = "0.8.0"
