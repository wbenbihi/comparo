"""Kind-specific resource detail widgets and their explicit registry."""

from .base import BaseKindResourceDetails as BaseKindResourceDetails
from .registry import RESOURCE_WIDGETS as RESOURCE_WIDGETS
from .registry import resource_widget as resource_widget

__all__ = ["RESOURCE_WIDGETS", "BaseKindResourceDetails", "resource_widget"]
