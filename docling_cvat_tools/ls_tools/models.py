"""Data models for Label Studio to DoclingDocument conversion.

This module defines LS-native data models that avoid CVAT-specific baggage
and provide a cleaner interface for the deterministic conversion pipeline.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

from docling_core.types.doc.base import BoundingBox
from docling_core.types.doc.document import ContentLayer
from docling_core.types.doc.labels import DocItemLabel, GraphCellLabel

from docling_cvat_tools.cvat_tools.models import TableStructLabel


@dataclass
class LSElement:
    """A rectangle annotation from Label Studio.

    Unlike CVATElement, this is a simple dataclass with `ls_id` as a first-class
    field and no CVAT-specific attributes like bbox_unrotated.
    """

    ls_id: str  # LS region string ID (e.g. "r_d2jd7dz11")
    int_id: int  # Sequential integer ID for internal use
    label: Union[DocItemLabel, GraphCellLabel, TableStructLabel]
    bbox: BoundingBox  # Pixel coordinates, TOP_LEFT origin
    content_layer: ContentLayer
    parent_ls_id: Optional[str] = None
    level: Optional[int] = None  # Explicitly set level, or None (unset)
    text: Optional[str] = None
    picture_type: Optional[str] = None
    rotation_deg: float = 0.0


@dataclass
class LSPath:
    """A polyline path with explicit connected regions.

    The connectedRegions field provides deterministic element references
    rather than requiring point-to-element proximity matching.
    """

    ls_id: str
    int_id: int
    label: str  # reading_order, merge, group, to_caption, to_footnote, to_value
    connected_region_ids: List[str]  # Ordered list from connectedRegions
    level: Optional[int] = None


@dataclass
class LSDocument:
    """Parsed Label Studio annotation document.

    Contains all elements and paths with lookup indices for efficient access.
    """

    elements: List[LSElement]
    paths: List[LSPath]
    element_by_ls_id: Dict[str, LSElement]
    element_by_int_id: Dict[int, LSElement]
    image_width: float
    image_height: float
    image_url: str


@dataclass
class ResolvedPaths:
    """Resolved path mappings using connectedRegions.

    All paths are resolved to their actual LSElement references via
    direct dictionary lookups - no proximity thresholds or spatial matching.
    """

    reading_orders: List[List[LSElement]]  # Each path's ordered element sequence
    merges: List[List[LSElement]]  # Each merge path's element group
    groups: List[List[LSElement]]  # Each group path's element set
    captions: List[tuple]  # (container, caption) pairs
    footnotes: List[tuple]  # (container, footnote) pairs
    to_values: List[tuple]  # (key, value) pairs


@dataclass
class ListState:
    """State tracking for list hierarchy processing via level propagation.

    This implements the level propagation model where:
    - Elements default to level=None (unset)
    - current_level tracks the last explicitly set level
    - Group paths define list boundaries explicitly
    - Without group paths, lists terminate on lower/unset levels
    """

    active_group_id: Optional[str] = None  # Current group path ls_id
    level_stack: List[int] = field(default_factory=list)  # Stack of open list levels
    current_level: Optional[int] = None  # Last explicitly set level (propagated)
    group_elements: Dict[str, Set[str]] = field(
        default_factory=dict
    )  # group_id → {element ls_ids}


# Type alias for element label types
ElementLabel = Union[DocItemLabel, GraphCellLabel, TableStructLabel]
