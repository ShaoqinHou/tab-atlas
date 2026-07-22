"""Compatibility facade for resource and report projection helpers."""

from .resource_projection import (
    report_collection_summaries,
    report_group_summaries,
    report_source_summaries,
    resource_presentation,
)

__all__ = [
    "report_collection_summaries",
    "report_group_summaries",
    "report_source_summaries",
    "resource_presentation",
]
