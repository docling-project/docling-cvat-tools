"""Main conversion pipeline from Label Studio annotations to DoclingDocument.

This module implements a deterministic conversion pipeline that uses the
connectedRegions field to resolve path-to-element mappings without heuristics.

Pipeline Steps:
1. Parse regions → LSElement and LSPath objects
2. Resolve connectedRegions → direct element lookups
3. Build containment tree → reuse from cvat_tools
4. Build global reading order → concatenate reading_order paths
5. Build DoclingDocument → walk reading order, create items
6. Apply relationship paths → merges, captions, footnotes, to_values
7. Generate outputs → HTML, tree view
"""

import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from docling_core.types.doc.base import BoundingBox, CoordOrigin, ImageRefMode
from docling_core.types.doc.document import (
    ContentLayer,
    DocItem,
    DocItemLabel,
    DoclingDocument,
    FloatingItem,
    GraphCell,
    GraphData,
    GraphLink,
    GroupItem,
    GroupLabel,
    ImageRef,
    ListItem,
    NodeItem,
    PictureClassificationClass,
    PictureClassificationData,
    ProvenanceItem,
    RefItem,
    Size,
    TableData,
)
from docling_core.types.doc.labels import GraphCellLabel, GraphLinkLabel
from PIL import Image

from docling_cvat_tools.cvat_tools.geometry import bbox_intersection
from docling_cvat_tools.cvat_tools.models import CVATElement, TableStructLabel
from docling_cvat_tools.cvat_tools.tree import TreeNode, build_containment_tree
from docling_cvat_tools.utils import classify_cells, sort_cell_ids

from .list_hierarchy import build_element_to_groups, build_group_membership
from .models import LSDocument, LSElement, LSPath, ResolvedPaths

logger = logging.getLogger(__name__)


# Label mappings from Label Studio label strings
LABEL_MAPPING: Dict[str, Any] = {
    "text": DocItemLabel.TEXT,
    "section_header": DocItemLabel.SECTION_HEADER,
    "caption": DocItemLabel.CAPTION,
    "footnote": DocItemLabel.FOOTNOTE,
    "list_item": DocItemLabel.LIST_ITEM,
    "picture": DocItemLabel.PICTURE,
    "table": DocItemLabel.TABLE,
    "formula": DocItemLabel.FORMULA,
    "code": DocItemLabel.CODE,
    "document_index": DocItemLabel.DOCUMENT_INDEX,
    "form": DocItemLabel.FORM,
    "page_header": DocItemLabel.PAGE_HEADER,
    "page_footer": DocItemLabel.PAGE_FOOTER,
    "handwritten_text": DocItemLabel.HANDWRITTEN_TEXT,
    "checkbox_selected": DocItemLabel.CHECKBOX_SELECTED,
    "checkbox_unselected": DocItemLabel.CHECKBOX_UNSELECTED,
    "grading_scale": DocItemLabel.GRADING_SCALE,
    "empty_value": DocItemLabel.EMPTY_VALUE,
    "key": GraphCellLabel.KEY,
    "value": GraphCellLabel.VALUE,
    "table_row": TableStructLabel.TABLE_ROW,
    "table_column": TableStructLabel.TABLE_COLUMN,
    "table_merged_cell": TableStructLabel.TABLE_MERGED_CELL,
    "col_header": TableStructLabel.COL_HEADER,
    "row_header": TableStructLabel.ROW_HEADER,
    "table_row_section": TableStructLabel.ROW_SECTION,
    "body": TableStructLabel.BODY,
}

CONTENT_LAYER_MAPPING: Dict[str, ContentLayer] = {
    "BODY": ContentLayer.BODY,
    "FURNITURE": ContentLayer.FURNITURE,
    "BACKGROUND": ContentLayer.BACKGROUND,
    "body": ContentLayer.BODY,
    "furniture": ContentLayer.FURNITURE,
    "background": ContentLayer.BACKGROUND,
}

PATH_LABELS = {
    "reading_order",
    "merge",
    "group",
    "to_caption",
    "to_footnote",
    "to_value",
}

# Picture type mapping
PIC_CLASSES = {
    "BARCODE": "bar_code",
    "CHART": "chart",
    "DECORATION": "decoration",
    "ILLUSTRATION": "illustration",
    "INFOGRAPHIC": "infographic",
    "LOGO": "logo",
    "OTHER": "other",
    "PERSON": "person",
    "PICTOGRAM": "icon",
    "SCREENSHOT": "screenshot",
    "UI_ELEMENT": "ui_element",
}


@dataclass
class ConversionResult:
    """Result of converting LS annotations to DoclingDocument."""

    html: str
    visualization_base64: Optional[str]
    validation_errors: List[Dict[str, Any]]
    docling_document: Optional[DoclingDocument]
    has_blocking_errors: bool = False
    tree_html: str = ""


@dataclass
class Cell:
    """Table cell with grid coordinates and attributes."""

    start_row: int
    end_row: int
    start_column: int
    end_column: int
    row_span_length: int
    column_span_length: int
    bbox: BoundingBox
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False
    fillable_cell: bool = False


def download_image(
    url: str, headers: Optional[Dict[str, str]] = None
) -> Optional[Image.Image]:
    """Download image from URL and return as PIL Image.

    Args:
        url: Image URL to download
        headers: Optional HTTP headers (e.g., for authentication)

    Returns:
        PIL Image or None if download fails
    """
    if not url:
        return None
    try:
        import httpx

        response = httpx.get(
            url, timeout=10.0, follow_redirects=True, headers=headers or {}
        )
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except Exception as e:
        logger.warning(f"Failed to download image from {url}: {e}")
        return None


def parse_ls_region_to_element(
    region: Dict[str, Any],
    image_width: float,
    image_height: float,
    int_id: int,
) -> Optional[LSElement]:
    """Parse a Label Studio rectangle region into an LSElement.

    Args:
        region: Label Studio region dictionary
        image_width: Image width in pixels
        image_height: Image height in pixels
        int_id: Sequential integer ID for internal use

    Returns:
        LSElement or None if parsing fails
    """
    ls_id = region.get("id", f"elem_{int_id}")

    # Extract label
    label_str = None
    if "rectanglelabels" in region and region["rectanglelabels"]:
        label_str = region["rectanglelabels"][0].lower()
    elif "label" in region:
        label_str = region["label"].lower()

    if not label_str or label_str not in LABEL_MAPPING:
        logger.warning(f"Unknown label: {label_str}")
        return None

    label = LABEL_MAPPING[label_str]

    # Convert percentage coordinates to pixel coordinates
    x_pct = region.get("x", 0)
    y_pct = region.get("y", 0)
    width_pct = region.get("width", 0)
    height_pct = region.get("height", 0)

    x = (x_pct / 100.0) * image_width
    y = (y_pct / 100.0) * image_height
    width = (width_pct / 100.0) * image_width
    height = (height_pct / 100.0) * image_height

    bbox = BoundingBox(
        l=x,
        t=y,
        r=x + width,
        b=y + height,
        coord_origin=CoordOrigin.TOPLEFT,
    )

    # Extract content layer
    content_layer_str = region.get("content_layer", "BODY")
    content_layer = CONTENT_LAYER_MAPPING.get(content_layer_str, ContentLayer.BODY)

    # Extract optional attributes
    level = region.get("level")
    if level is not None:
        try:
            level = int(level)
        except (ValueError, TypeError):
            level = None

    picture_type = region.get("picture_type")
    rotation = region.get("rotation", 0.0)
    text_content = region.get("text") or None
    parent_ls_id = region.get("parent_id")
    if not isinstance(parent_ls_id, str) or not parent_ls_id:
        parent_ls_id = None

    return LSElement(
        ls_id=ls_id,
        int_id=int_id,
        label=label,
        bbox=bbox,
        content_layer=content_layer,
        parent_ls_id=parent_ls_id,
        level=level,
        text=text_content,
        picture_type=picture_type,
        rotation_deg=rotation,
    )


def parse_ls_region_to_path(
    region: Dict[str, Any],
    image_width: float,
    image_height: float,
    int_id: int,
) -> Optional[LSPath]:
    """Parse a Label Studio polyline region into an LSPath.

    Args:
        region: Label Studio region dictionary
        image_width: Image width in pixels
        image_height: Image height in pixels
        int_id: Sequential integer ID

    Returns:
        LSPath or None if parsing fails
    """
    ls_id = region.get("id", f"path_{int_id}")

    # Extract label
    label_str = None
    if "polylinelabels" in region and region["polylinelabels"]:
        label_str = region["polylinelabels"][0].lower()
    elif "label" in region:
        label_str = region["label"].lower()

    if not label_str:
        logger.warning(f"No label found for polyline region {ls_id}")
        return None

    # Normalize reading_order variants
    if label_str.startswith("reading_order"):
        label_str = "reading_order"

    if label_str not in PATH_LABELS:
        logger.warning(f"Unknown path label: {label_str}")
        return None

    # Extract connectedRegions (list of region IDs)
    connected_regions = region.get("connectedRegions", [])
    if not connected_regions:
        logger.warning(f"Path {ls_id} has no connectedRegions - skipping")
        return None

    if not isinstance(connected_regions, list):
        logger.warning(f"Path {ls_id} connectedRegions is not a list - skipping")
        return None

    connected_region_ids = [
        entry for entry in connected_regions if isinstance(entry, str) and entry
    ]

    if len(connected_region_ids) < 1:
        logger.warning(f"Path {ls_id} has no valid connected region IDs")
        return None

    # Extract level attribute
    level = region.get("level")
    if level is not None:
        try:
            level = int(level)
        except (ValueError, TypeError):
            level = None

    return LSPath(
        ls_id=ls_id,
        int_id=int_id,
        label=label_str,
        connected_region_ids=connected_region_ids,
        level=level,
    )


def parse_regions(
    regions: List[Dict[str, Any]],
    image_width: float,
    image_height: float,
    image_url: str = "",
) -> LSDocument:
    """Parse Label Studio regions into an LSDocument.

    Args:
        regions: List of Label Studio region dictionaries
        image_width: Image width in pixels
        image_height: Image height in pixels
        image_url: URL of the source image

    Returns:
        LSDocument with parsed elements and paths
    """
    elements: List[LSElement] = []
    paths: List[LSPath] = []
    element_by_ls_id: Dict[str, LSElement] = {}
    element_by_int_id: Dict[int, LSElement] = {}

    element_int_id = 0
    path_int_id = 0

    for region in regions:
        region_type = region.get("type", "").lower()

        if region_type in ["rectanglelabels", "rectangle"]:
            element = parse_ls_region_to_element(
                region, image_width, image_height, element_int_id
            )
            if element:
                elements.append(element)
                element_by_ls_id[element.ls_id] = element
                element_by_int_id[element.int_id] = element
                element_int_id += 1
        elif region_type in ["polylinelabels", "polyline"]:
            path = parse_ls_region_to_path(
                region, image_width, image_height, path_int_id
            )
            if path:
                paths.append(path)
                path_int_id += 1

    return LSDocument(
        elements=elements,
        paths=paths,
        element_by_ls_id=element_by_ls_id,
        element_by_int_id=element_by_int_id,
        image_width=image_width,
        image_height=image_height,
        image_url=image_url,
    )


def resolve_connected_regions(
    doc: LSDocument,
) -> Tuple[ResolvedPaths, List[LSPath]]:
    """Resolve connectedRegions to actual elements via dictionary lookup.

    Args:
        doc: Parsed LSDocument

    Returns:
        Tuple of (ResolvedPaths, list of group LSPath objects for reference)
    """
    reading_orders: List[List[LSElement]] = []
    merges: List[List[LSElement]] = []
    groups: List[List[LSElement]] = []
    captions: List[Tuple[LSElement, LSElement]] = []
    footnotes: List[Tuple[LSElement, LSElement]] = []
    to_values: List[Tuple[LSElement, LSElement]] = []
    group_paths: List[LSPath] = []

    for path in doc.paths:
        resolved_elements: List[LSElement] = []

        for region_id in path.connected_region_ids:
            element = doc.element_by_ls_id.get(region_id)
            if element:
                resolved_elements.append(element)
            else:
                logger.warning(f"Unknown region ID in connectedRegions: {region_id}")

        if not resolved_elements:
            logger.warning(
                f"Path {path.ls_id} ({path.label}) has no resolvable elements - skipping"
            )
            continue

        if path.label == "reading_order":
            reading_orders.append(resolved_elements)
        elif path.label == "merge":
            if len(resolved_elements) >= 2:
                merges.append(resolved_elements)
        elif path.label == "group":
            groups.append(resolved_elements)
            group_paths.append(path)
        elif path.label == "to_caption":
            if len(resolved_elements) >= 2:
                captions.append((resolved_elements[0], resolved_elements[1]))
        elif path.label == "to_footnote":
            if len(resolved_elements) >= 2:
                footnotes.append((resolved_elements[0], resolved_elements[1]))
        elif path.label == "to_value":
            if len(resolved_elements) >= 2:
                to_values.append((resolved_elements[0], resolved_elements[1]))

    resolved = ResolvedPaths(
        reading_orders=reading_orders,
        merges=merges,
        groups=groups,
        captions=captions,
        footnotes=footnotes,
        to_values=to_values,
    )

    return resolved, group_paths


def build_global_reading_order(resolved: ResolvedPaths) -> List[LSElement]:
    """Build global reading order by concatenating all reading_order paths.

    The invariant guarantees each element appears exactly once across all
    reading_order paths, so simple concatenation gives the complete order.

    Args:
        resolved: Resolved path mappings

    Returns:
        Flat ordered list of all elements in reading order
    """
    global_order: List[LSElement] = []
    seen: Set[str] = set()

    for path_elements in resolved.reading_orders:
        for element in path_elements:
            if element.ls_id not in seen:
                global_order.append(element)
                seen.add(element.ls_id)

    return global_order


def ls_element_to_cvat_element(element: LSElement) -> CVATElement:
    """Convert LSElement to CVATElement for compatibility with cvat_tools utilities.

    Args:
        element: LSElement to convert

    Returns:
        CVATElement with equivalent data
    """
    return CVATElement(
        id=element.int_id,
        label=element.label,
        bbox=element.bbox,
        rotation_deg=element.rotation_deg,
        content_layer=element.content_layer,
        type=element.picture_type,
        level=element.level,
        attributes={},
        text=element.text,
    )


def compute_cells(
    rows: List[LSElement],
    columns: List[LSElement],
    merges: List[LSElement],
    col_headers: List[LSElement],
    row_headers: List[LSElement],
    row_sections: List[LSElement],
    fillable_cells: List[LSElement],
    row_overlap_threshold: float = 0.5,
    col_overlap_threshold: float = 0.5,
) -> List[Cell]:
    """Compute table cells from row/column/merge annotations.

    This is adapted from cvat_to_docling.compute_cells but operates on LSElements.

    Args:
        rows: List of table_row elements
        columns: List of table_column elements
        merges: List of table_merged_cell elements
        col_headers: List of col_header elements
        row_headers: List of row_header elements
        row_sections: List of table_row_section elements
        fillable_cells: List of fillable_cells elements
        row_overlap_threshold: Overlap threshold for row matching
        col_overlap_threshold: Overlap threshold for column matching

    Returns:
        List of Cell objects
    """
    rows = sorted(rows, key=lambda r: (r.bbox.t + r.bbox.b) / 2.0)
    columns = sorted(columns, key=lambda c: (c.bbox.l + c.bbox.r) / 2.0)

    n_rows, n_cols = len(rows), len(columns)

    def span_from_merge(
        m: BoundingBox,
        lines: List[LSElement],
        axis: str,
        frac_threshold: float,
    ) -> Optional[Tuple[int, int]]:
        """Map a merge bbox to an inclusive index span."""
        idxs = []
        best_i, best_len = None, 0.0
        for i, elem in enumerate(lines):
            inter = bbox_intersection(m, elem.bbox)
            if not inter:
                continue
            if axis == "row":
                overlap_len = inter.height
                base = max(1e-9, elem.bbox.height)
            else:
                overlap_len = inter.width
                base = max(1e-9, elem.bbox.width)

            frac = overlap_len / base
            if frac >= frac_threshold:
                idxs.append(i)

            if overlap_len > best_len:
                best_len, best_i = overlap_len, i

        if idxs:
            return min(idxs), max(idxs)
        if best_i is not None and best_len > 0.0:
            return best_i, best_i
        return None

    def is_bbox_within(
        bbox_a: BoundingBox, bbox_b: BoundingBox, threshold: float = 0.5
    ) -> bool:
        """Check if bbox_b lies within bbox_a."""
        inter = bbox_intersection(bbox_a, bbox_b)
        if not inter:
            return False
        return inter.area() / max(bbox_b.area(), 1e-9) >= threshold

    def process_table_headers(bbox: BoundingBox) -> Tuple[bool, bool, bool, bool]:
        """Check if cell overlaps with header regions."""
        c_col_header = any(is_bbox_within(h.bbox, bbox) for h in col_headers)
        c_row_header = any(is_bbox_within(h.bbox, bbox) for h in row_headers)
        c_row_section = any(is_bbox_within(h.bbox, bbox) for h in row_sections)
        c_fillable = any(is_bbox_within(h.bbox, bbox) for h in fillable_cells)
        return c_col_header, c_row_header, c_row_section, c_fillable

    cells: List[Cell] = []
    covered: Set[Tuple[int, int]] = set()
    seen_merge_rects: Set[Tuple[int, int, int, int]] = set()

    # Add merged cells first
    for m in merges:
        rspan = span_from_merge(
            m.bbox, rows, axis="row", frac_threshold=row_overlap_threshold
        )
        cspan = span_from_merge(
            m.bbox, columns, axis="col", frac_threshold=col_overlap_threshold
        )
        if rspan is None or cspan is None:
            continue

        sr, er = rspan
        sc, ec = cspan
        rect_key = (sr, er, sc, ec)
        if rect_key in seen_merge_rects:
            continue
        seen_merge_rects.add(rect_key)

        grid_bbox = BoundingBox(
            l=columns[sc].bbox.l,
            t=rows[sr].bbox.t,
            r=columns[ec].bbox.r,
            b=rows[er].bbox.b,
            coord_origin=CoordOrigin.TOPLEFT,
        )
        c_col_header, c_row_header, c_row_section, c_fillable = process_table_headers(
            grid_bbox
        )

        cells.append(
            Cell(
                start_row=sr,
                end_row=er,
                start_column=sc,
                end_column=ec,
                row_span_length=er - sr + 1,
                column_span_length=ec - sc + 1,
                bbox=grid_bbox,
                column_header=c_col_header,
                row_header=c_row_header,
                row_section=c_row_section,
                fillable_cell=c_fillable,
            )
        )

        for ri in range(sr, er + 1):
            for ci in range(sc, ec + 1):
                covered.add((ri, ci))

    # Add simple 1x1 cells
    for ri, row in enumerate(rows):
        for ci, col in enumerate(columns):
            if (ri, ci) in covered:
                continue
            inter = bbox_intersection(row.bbox, col.bbox)
            if not inter:
                continue
            c_col_header, c_row_header, c_row_section, c_fillable = (
                process_table_headers(inter)
            )
            cells.append(
                Cell(
                    start_row=ri,
                    end_row=ri,
                    start_column=ci,
                    end_column=ci,
                    row_span_length=1,
                    column_span_length=1,
                    bbox=inter,
                    column_header=c_col_header,
                    row_header=c_row_header,
                    row_section=c_row_section,
                    fillable_cell=c_fillable,
                )
            )

    return cells


def convert_bbox_to_bottomleft(
    bbox: BoundingBox,
    page_height: float,
) -> BoundingBox:
    """Convert TOPLEFT bbox to BOTTOMLEFT coordinates.

    Args:
        bbox: BoundingBox with TOPLEFT origin
        page_height: Height of the page

    Returns:
        BoundingBox with BOTTOMLEFT origin
    """
    return BoundingBox(
        l=bbox.l,
        t=page_height - bbox.t,
        r=bbox.r,
        b=page_height - bbox.b,
        coord_origin=CoordOrigin.BOTTOMLEFT,
    )


def create_provenance(
    element: LSElement,
    page_no: int,
    page_height: float,
) -> ProvenanceItem:
    """Create provenance item for an element.

    Args:
        element: The element to create provenance for
        page_no: Page number (1-indexed)
        page_height: Height of the page for coordinate conversion

    Returns:
        ProvenanceItem with BOTTOMLEFT coordinates
    """
    bbox_bl = convert_bbox_to_bottomleft(element.bbox, page_height)
    return ProvenanceItem(page_no=page_no, bbox=bbox_bl, charspan=(0, 0))


def is_container_label(label: Any) -> bool:
    """Check if a label represents a container element."""
    container_labels = {
        DocItemLabel.TABLE,
        DocItemLabel.PICTURE,
        DocItemLabel.FORM,
    }
    return label in container_labels


def is_table_structure_label(label: Any) -> bool:
    """Check if a label is a table structure label."""
    return isinstance(label, TableStructLabel)


@dataclass
class DocumentBuilder:
    """Builder class for constructing DoclingDocument from LSDocument."""

    doc: DoclingDocument
    ls_doc: LSDocument
    resolved: ResolvedPaths
    containment_tree: List[TreeNode]
    tree_index: Dict[int, TreeNode]
    group_paths: List[LSPath]

    # Track created items by element ls_id
    element_to_item: Dict[str, NodeItem] = field(default_factory=dict)

    # Track inferred levels in reading order
    effective_levels: Dict[str, int] = field(default_factory=dict)

    # Track list hierarchy
    element_to_groups: Dict[str, List[str]] = field(default_factory=dict)
    group_containers: Dict[str, GroupItem] = field(default_factory=dict)
    standalone_list_counter: int = 0
    active_list_group_id: Optional[str] = None
    active_list_container: Optional[GroupItem] = None
    active_list_base_level: Optional[int] = None
    last_list_item_by_level: Dict[int, ListItem] = field(default_factory=dict)
    sublist_containers: Dict[Tuple[str, int], GroupItem] = field(default_factory=dict)

    def build(self, page_height: float) -> None:
        """Build the document by processing reading order.

        Args:
            page_height: Height of the page for coordinate conversion
        """
        # Build group membership mappings
        group_membership = build_group_membership(
            self.resolved.groups, self.group_paths
        )
        self.element_to_groups = build_element_to_groups(group_membership)

        # Process elements in reading order
        global_order = build_global_reading_order(self.resolved)
        self._compute_effective_levels(global_order)

        for element in global_order:
            self._process_element(element, page_height)

        # Reading-order paths only cover top-level flow. Explicitly contained
        # children still need to be attached to their parent items.
        self._process_remaining_children(page_height)

        # Apply relationship paths
        self._apply_caption_paths(page_height)
        self._apply_footnote_paths(page_height)
        self._apply_to_value_paths(page_height)
        self._apply_merge_paths()

    def _compute_effective_levels(self, global_order: List[LSElement]) -> None:
        """Infer effective levels from reading order.

        Unset levels inherit the last explicit level. If no prior explicit
        level exists, the effective level defaults to 1.
        """
        current_level: Optional[int] = None
        for element in global_order:
            if element.level is not None:
                current_level = element.level
            self.effective_levels[element.ls_id] = (
                current_level if current_level is not None else 1
            )

    def _effective_level(self, element: LSElement) -> int:
        """Return the inferred level for an element."""
        return self.effective_levels.get(
            element.ls_id, element.level if element.level is not None else 1
        )

    def _process_element(self, element: LSElement, page_height: float) -> None:
        """Process a single element and add it to the document.

        Args:
            element: Element to process
            page_height: Page height for coordinate conversion
        """
        # Skip table structure elements - they're processed with their parent table
        if is_table_structure_label(element.label):
            return

        if not isinstance(element.label, GraphCellLabel) and (
            element.label != DocItemLabel.LIST_ITEM
        ):
            self._reset_list_context()

        # Create the item based on label type
        item = self._create_item(element, page_height)
        if item:
            self.element_to_item[element.ls_id] = item

    def _create_item(
        self, element: LSElement, page_height: float
    ) -> Optional[NodeItem]:
        """Create a document item for an element.

        Args:
            element: Element to create item for
            page_height: Page height for coordinate conversion

        Returns:
            Created NodeItem or None
        """
        prov = create_provenance(element, page_no=1, page_height=page_height)
        parent = self._determine_parent(element)
        text = element.text or ""

        label = element.label

        if label == DocItemLabel.TEXT:
            return self.doc.add_text(
                label=DocItemLabel.TEXT, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.SECTION_HEADER:
            level = self._effective_level(element)
            return self.doc.add_heading(
                text=text, level=level, prov=prov, parent=parent
            )

        elif label == DocItemLabel.LIST_ITEM:
            return self._create_list_item(element, prov, parent)

        elif label == DocItemLabel.TABLE:
            return self._create_table(element, prov, page_height, parent)

        elif label == DocItemLabel.PICTURE:
            return self._create_picture(element, prov, parent)

        elif label == DocItemLabel.CAPTION:
            return self.doc.add_text(
                label=DocItemLabel.CAPTION, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.FOOTNOTE:
            return self.doc.add_text(
                label=DocItemLabel.FOOTNOTE, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.CODE:
            return self.doc.add_code(text=text, prov=prov, parent=parent)

        elif label == DocItemLabel.FORMULA:
            return self.doc.add_text(
                label=DocItemLabel.FORMULA, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.PAGE_HEADER:
            return self.doc.add_text(
                label=DocItemLabel.PAGE_HEADER, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.PAGE_FOOTER:
            return self.doc.add_text(
                label=DocItemLabel.PAGE_FOOTER, text=text, prov=prov, parent=parent
            )

        elif label == DocItemLabel.FORM:
            return self.doc.add_group(
                label=GroupLabel.FORM_AREA, name="form", parent=parent
            )

        elif isinstance(label, GraphCellLabel):
            # Graph cells are materialized through key-value graphs instead of as
            # standalone text items in the main document flow.
            return None

        else:
            # Generic text fallback
            return self.doc.add_text(
                label=DocItemLabel.TEXT, text=text, prov=prov, parent=parent
            )

    def _process_remaining_children(self, page_height: float) -> None:
        """Create contained elements that are not present in reading order."""
        pending = [
            element
            for element in self.ls_doc.elements
            if element.ls_id not in self.element_to_item
            and not is_table_structure_label(element.label)
            and not isinstance(element.label, GraphCellLabel)
        ]

        while pending:
            progressed = False
            next_pending: List[LSElement] = []

            for element in pending:
                if self._has_unresolved_parent(element):
                    next_pending.append(element)
                    continue

                item = self._create_item(element, page_height)
                if item:
                    self.element_to_item[element.ls_id] = item
                progressed = True

            if not progressed:
                for element in next_pending:
                    logger.warning(
                        "Creating element %s without resolved parent %s",
                        element.ls_id,
                        element.parent_ls_id,
                    )
                    item = self._create_item(element, page_height)
                    if item:
                        self.element_to_item[element.ls_id] = item
                break

            pending = next_pending

    def _has_unresolved_parent(self, element: LSElement) -> bool:
        """Check whether an element has an explicit parent that is not ready."""
        if (
            element.parent_ls_id is not None
            and element.parent_ls_id not in self.element_to_item
        ):
            return True

        tree_node = self._find_tree_node(element)
        if tree_node and tree_node.parent:
            parent_element = self.ls_doc.element_by_int_id.get(
                tree_node.parent.element.id
            )
            if (
                parent_element is not None
                and parent_element.ls_id not in self.element_to_item
            ):
                return True

        return False

    def _create_list_item(
        self,
        element: LSElement,
        prov: ProvenanceItem,
        default_parent: Optional[NodeItem],
    ) -> Optional[ListItem]:
        """Create a list item with proper hierarchy tracking.

        Args:
            element: The list_item element
            prov: Provenance item
            default_parent: Default parent if not in a list

        Returns:
            Created ListItem
        """
        text = element.text or ""
        effective_level = self._effective_level(element)
        element_groups = self.element_to_groups.get(element.ls_id, [])
        group_id = element_groups[0] if element_groups else None

        if self.active_list_container is None or self.active_list_group_id != group_id:
            self._activate_list_context(
                group_id=group_id,
                parent=default_parent,
                starting_level=effective_level,
            )

        # Returning to a shallower level invalidates deeper sibling state.
        for level in list(self.last_list_item_by_level):
            if level > effective_level:
                self.last_list_item_by_level.pop(level, None)

        if self.active_list_base_level is None:
            self.active_list_base_level = effective_level

        parent_item: Optional[NodeItem]
        if effective_level <= self.active_list_base_level:
            self.active_list_base_level = effective_level
            parent_item = self.active_list_container
        else:
            parent_item = self._get_or_create_sublist_container(
                level=effective_level,
                fallback_parent=self.active_list_container,
            )

        list_item = self.doc.add_list_item(text=text, prov=prov, parent=parent_item)
        self.last_list_item_by_level[effective_level] = list_item

        return list_item

    def _activate_list_context(
        self,
        group_id: Optional[str],
        parent: Optional[NodeItem],
        starting_level: int,
    ) -> None:
        """Start a new list context for a group or standalone list."""
        self._reset_list_context()
        self.active_list_group_id = group_id
        self.active_list_base_level = starting_level

        if group_id is not None:
            if group_id not in self.group_containers:
                self.group_containers[group_id] = self.doc.add_group(
                    label=GroupLabel.LIST,
                    name=f"list_{group_id}",
                    parent=parent,
                )
            self.active_list_container = self.group_containers[group_id]
            return

        self.standalone_list_counter += 1
        self.active_list_container = self.doc.add_group(
            label=GroupLabel.LIST,
            name=f"list_{self.standalone_list_counter}",
            parent=parent,
        )

    def _get_or_create_sublist_container(
        self,
        level: int,
        fallback_parent: Optional[NodeItem],
    ) -> Optional[NodeItem]:
        """Return the container that should hold items for the given level."""
        parent_level = max(
            (
                known_level
                for known_level in self.last_list_item_by_level
                if known_level < level
            ),
            default=None,
        )
        if parent_level is None:
            return fallback_parent

        anchor_item = self.last_list_item_by_level[parent_level]
        key = (anchor_item.self_ref, level)
        if key not in self.sublist_containers:
            self.sublist_containers[key] = self.doc.add_group(
                label=GroupLabel.LIST,
                name=f"sublist_{anchor_item.self_ref.split('/')[-1]}_{level}",
                parent=anchor_item,
            )

        return self.sublist_containers[key]

    def _reset_list_context(self) -> None:
        """Clear active list-tracking state."""
        self.active_list_group_id = None
        self.active_list_container = None
        self.active_list_base_level = None
        self.last_list_item_by_level.clear()

    def _create_table(
        self,
        element: LSElement,
        prov: ProvenanceItem,
        page_height: float,
        parent: Optional[NodeItem],
    ) -> Optional[NodeItem]:
        """Create a table with cells from table structure elements.

        Args:
            element: The table element
            prov: Provenance item
            page_height: Page height for coordinate conversion
            parent: Parent node

        Returns:
            Created table item
        """
        # Find table structure children using containment tree
        tree_node = self._find_tree_node(element)
        if not tree_node:
            return self.doc.add_table(
                data=TableData(num_rows=0, num_cols=0, table_cells=[]),
                prov=prov,
                parent=parent,
            )

        # Collect structure elements
        rows: List[LSElement] = []
        columns: List[LSElement] = []
        merges: List[LSElement] = []
        col_headers: List[LSElement] = []
        row_headers: List[LSElement] = []
        row_sections: List[LSElement] = []
        fillable_cells: List[LSElement] = []

        def collect_structure(node: TreeNode) -> None:
            el = self.ls_doc.element_by_int_id.get(node.element.id)
            if el is None:
                return
            if el.label == TableStructLabel.TABLE_ROW:
                rows.append(el)
            elif el.label == TableStructLabel.TABLE_COLUMN:
                columns.append(el)
            elif el.label == TableStructLabel.TABLE_MERGED_CELL:
                merges.append(el)
            elif el.label == TableStructLabel.COL_HEADER:
                col_headers.append(el)
            elif el.label == TableStructLabel.ROW_HEADER:
                row_headers.append(el)
            elif el.label == TableStructLabel.ROW_SECTION:
                row_sections.append(el)
            elif el.label == TableStructLabel.TABLE_FILLABLE_CELLS:
                fillable_cells.append(el)
            for child in node.children:
                collect_structure(child)

        for child in tree_node.children:
            collect_structure(child)

        if not rows or not columns:
            return self.doc.add_table(
                data=TableData(num_rows=0, num_cols=0, table_cells=[]),
                prov=prov,
                parent=parent,
            )

        # Compute cells
        cells = compute_cells(
            rows,
            columns,
            merges,
            col_headers,
            row_headers,
            row_sections,
            fillable_cells,
        )

        # Build table data
        from docling_core.types.doc import TableCell as DocTableCell

        table_cells = []
        for cell in cells:
            tc = DocTableCell(
                row_span=cell.row_span_length,
                col_span=cell.column_span_length,
                start_row_offset_idx=cell.start_row,
                end_row_offset_idx=cell.end_row,
                start_col_offset_idx=cell.start_column,
                end_col_offset_idx=cell.end_column,
                text="",  # Text could be extracted from overlapping text elements
                column_header=cell.column_header,
                row_header=cell.row_header,
                row_section=cell.row_section,
            )
            table_cells.append(tc)

        table_data = TableData(
            num_rows=len(rows),
            num_cols=len(columns),
            table_cells=table_cells,
        )

        return self.doc.add_table(data=table_data, prov=prov, parent=parent)

    def _create_picture(
        self,
        element: LSElement,
        prov: ProvenanceItem,
        parent: Optional[NodeItem],
    ) -> Optional[NodeItem]:
        """Create a picture element.

        Args:
            element: The picture element
            prov: Provenance item
            parent: Parent node

        Returns:
            Created picture item
        """
        # Handle picture classification
        classification = None
        if element.picture_type:
            mapped_class = PIC_CLASSES.get(element.picture_type.upper())
            if mapped_class:
                try:
                    classification = PictureClassificationData(
                        provenance="annotation",
                        predicted_classes=[
                            PictureClassificationClass(
                                class_name=mapped_class,
                                confidence=1.0,
                            )
                        ],
                    )
                except ValueError:
                    pass

        return self.doc.add_picture(
            annotations=[classification] if classification else None,
            prov=prov,
            parent=parent,
        )

    def _determine_parent(self, element: LSElement) -> Optional[NodeItem]:
        """Determine the parent for an element based on containment tree.

        Args:
            element: Element to find parent for

        Returns:
            Parent NodeItem or None
        """
        if element.parent_ls_id:
            explicit_parent = self.element_to_item.get(element.parent_ls_id)
            if explicit_parent is not None:
                return explicit_parent

        tree_node = self._find_tree_node(element)
        if tree_node and tree_node.parent:
            parent_element = self.ls_doc.element_by_int_id.get(
                tree_node.parent.element.id
            )
            if parent_element:
                return self.element_to_item.get(parent_element.ls_id)
        return None

    def _find_tree_node(self, element: LSElement) -> Optional[TreeNode]:
        """Find the tree node for an element.

        Args:
            element: Element to find

        Returns:
            TreeNode or None
        """
        return self.tree_index.get(element.int_id)

    def _apply_caption_paths(self, page_height: float) -> None:
        """Apply caption relationships."""
        for container_el, caption_el in self.resolved.captions:
            container_item = self.element_to_item.get(container_el.ls_id)
            caption_item = self.element_to_item.get(caption_el.ls_id)
            if (
                container_item
                and caption_item
                and isinstance(container_item, FloatingItem)
            ):
                # Link caption to container
                if hasattr(container_item, "captions"):
                    if caption_item not in container_item.captions:
                        container_item.captions.append(
                            RefItem(cref=f"#{caption_item.self_ref}")
                        )

    def _apply_footnote_paths(self, page_height: float) -> None:
        """Apply footnote relationships."""
        for container_el, footnote_el in self.resolved.footnotes:
            container_item = self.element_to_item.get(container_el.ls_id)
            footnote_item = self.element_to_item.get(footnote_el.ls_id)
            if (
                container_item
                and footnote_item
                and isinstance(container_item, FloatingItem)
            ):
                if hasattr(container_item, "footnotes"):
                    if footnote_item not in container_item.footnotes:
                        container_item.footnotes.append(
                            RefItem(cref=f"#{footnote_item.self_ref}")
                        )

    def _apply_to_value_paths(self, page_height: float) -> None:
        """Apply key-value relationships."""
        if not self.resolved.to_values:
            return

        cell_by_element: Dict[str, GraphCell] = {}
        links: List[GraphLink] = []
        next_cell_id = 0

        def make_cell(element: LSElement, default_label: GraphCellLabel) -> GraphCell:
            nonlocal next_cell_id

            if element.ls_id in cell_by_element:
                return cell_by_element[element.ls_id]

            item = self.element_to_item.get(element.ls_id)
            if item is None and element.parent_ls_id is not None:
                item = self.element_to_item.get(element.parent_ls_id)

            text = element.text or (getattr(item, "text", None) if item else "") or ""
            label = (
                element.label
                if isinstance(element.label, GraphCellLabel)
                else default_label
            )

            cell = GraphCell(
                label=label,
                cell_id=next_cell_id,
                text=text,
                orig=text,
                prov=create_provenance(element, page_no=1, page_height=page_height),
                item_ref=item.get_ref() if item is not None else None,
            )
            cell_by_element[element.ls_id] = cell
            next_cell_id += 1
            return cell

        for key_element, value_element in self.resolved.to_values:
            key_cell = make_cell(key_element, GraphCellLabel.KEY)
            value_cell = make_cell(value_element, GraphCellLabel.VALUE)
            links.append(
                GraphLink(
                    label=GraphLinkLabel.TO_VALUE,
                    source_cell_id=key_cell.cell_id,
                    target_cell_id=value_cell.cell_id,
                )
            )

        if not cell_by_element:
            return

        graph = GraphData(
            cells=sorted(cell_by_element.values(), key=lambda cell: cell.cell_id),
            links=links,
        )
        classify_cells(graph)
        self.doc.add_key_values(graph=graph, prov=None)
        sort_cell_ids(self.doc)

    def _apply_merge_paths(self) -> None:
        """Apply merge paths to combine text from merged elements."""
        for merge_elements in self.resolved.merges:
            if len(merge_elements) < 2:
                continue

            # Find the first item that was created
            first_item = None
            merged_text_parts = []

            for el in merge_elements:
                item = self.element_to_item.get(el.ls_id)
                if item:
                    if first_item is None:
                        first_item = item
                    if hasattr(item, "text") and item.text:
                        merged_text_parts.append(item.text)

            # Update first item with merged text
            if first_item and hasattr(first_item, "text"):
                first_item.text = " ".join(merged_text_parts)


def convert_ls_to_docling(
    regions: List[Dict[str, Any]],
    image_width: float,
    image_height: float,
    image_url: str = "",
    document_name: str = "document",
    image_headers: Optional[Dict[str, str]] = None,
) -> ConversionResult:
    """Convert Label Studio annotations to DoclingDocument.

    This is the main entry point for the deterministic conversion pipeline.

    Args:
        regions: List of Label Studio region dictionaries
        image_width: Image width in pixels
        image_height: Image height in pixels
        image_url: URL of the source image
        document_name: Name for the document
        image_headers: Optional HTTP headers for image download

    Returns:
        ConversionResult with DoclingDocument and rendered outputs
    """
    validation_errors: List[Dict[str, Any]] = []

    # Step 1: Parse regions
    ls_doc = parse_regions(regions, image_width, image_height, image_url)

    if not ls_doc.elements:
        return ConversionResult(
            html="<div class='page'><p>No annotations to render.</p></div>",
            visualization_base64=None,
            validation_errors=[
                {"type": "warning", "message": "No rectangle annotations found"}
            ],
            docling_document=None,
        )

    # Step 2: Resolve connectedRegions
    resolved, group_paths = resolve_connected_regions(ls_doc)

    # Step 3: Build containment tree using cvat_tools
    cvat_elements = [ls_element_to_cvat_element(el) for el in ls_doc.elements]
    tree_roots = build_containment_tree(cvat_elements)

    # Build tree index
    tree_index: Dict[int, TreeNode] = {}

    def index_tree(node: TreeNode) -> None:
        tree_index[node.element.id] = node
        for child in node.children:
            index_tree(child)

    for root in tree_roots:
        index_tree(root)

    # Step 4: Create DoclingDocument
    doc = DoclingDocument(name=document_name)

    # Add page with optional image
    page_image = download_image(image_url, image_headers) if image_url else None
    image_ref = None
    if page_image:
        try:
            image_ref = ImageRef.from_pil(page_image, dpi=72)
        except Exception as e:
            logger.warning(f"Failed to create ImageRef: {e}")

    doc.add_page(
        page_no=1,
        size=Size(width=image_width, height=image_height),
        image=image_ref,
    )

    # Step 5: Build document using DocumentBuilder
    builder = DocumentBuilder(
        doc=doc,
        ls_doc=ls_doc,
        resolved=resolved,
        containment_tree=tree_roots,
        tree_index=tree_index,
        group_paths=group_paths,
    )
    builder.build(page_height=image_height)

    # Step 6: Generate outputs
    try:
        html_content = doc.export_to_html(
            image_mode=ImageRefMode.EMBEDDED,
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
        )
    except Exception as e:
        logger.warning(f"Failed to export HTML: {e}")
        html_content = "<div class='page'><p>HTML export failed.</p></div>"

    # Generate tree view
    tree_html = _render_tree_html(doc)

    return ConversionResult(
        html=html_content,
        visualization_base64=None,
        validation_errors=validation_errors,
        docling_document=doc,
        has_blocking_errors=False,
        tree_html=tree_html,
    )


def _render_tree_html(doc: Optional[DoclingDocument]) -> str:
    """Render the DoclingDocument element tree as styled HTML.

    Args:
        doc: The DoclingDocument to render, or None

    Returns:
        HTML string showing the tree structure
    """
    if doc is None:
        return ""

    try:
        tree_text = doc.export_to_element_tree()
    except Exception:
        return ""

    import html as html_mod

    label_colors = {
        "text": "#FFFF99",
        "section_header": "#FF9999",
        "list_item": "#9999FF",
        "table": "#FFCCCC",
        "picture": "#FFCCA4",
        "caption": "#FFCC99",
        "footnote": "#C8C8FF",
        "formula": "#C0C0C0",
        "code": "#7D7D7D",
        "form": "#C8FFFF",
        "key_value_region": "#FFD9B3",
        "key": "#FFB3B3",
        "value": "#B3FFB3",
        "group": "#E0E0E0",
        "ordered_list": "#B3B3FF",
        "unordered_list": "#B3B3FF",
    }
    light_text_labels = {"list_item", "code"}

    html_parts = [
        "<!DOCTYPE html>",
        "<html><head>",
        "<style>",
        "body { font-family: 'Monaco', 'Consolas', monospace; font-size: 11px; "
        "margin: 0; padding: 10px; background: #fafafa; }",
        ".tree { line-height: 1.6; white-space: pre; }",
        ".tree-header { font-weight: bold; margin-bottom: 10px; padding-bottom: 5px; "
        "border-bottom: 1px solid #ddd; }",
        ".tree-line { display: block; }",
        ".tree-label { display: inline-block; padding: 1px 6px; border-radius: 3px; "
        "font-weight: bold; margin-right: 4px; }",
        ".tree-text { color: #555; font-style: italic; }",
        "</style>",
        "</head><body>",
        "<div class='tree'>",
        "<div class='tree-header'>Document Tree Structure</div>",
    ]

    for line in tree_text.splitlines():
        if not line.strip():
            continue

        stripped = line.lstrip(" ")
        depth = len(line) - len(stripped)
        indent = "│ " * depth

        parts = stripped.split(": ", 2)
        if len(parts) >= 2:
            label = parts[1].split(":")[0].split(" with ")[0].strip()
        else:
            label = stripped

        text_portion = ""
        if len(parts) >= 3:
            text_portion = parts[2]
        elif len(parts) == 2 and ": " in stripped[stripped.index(": ") + 2 :]:
            rest = stripped[stripped.index(": ") + 2 :]
            if ": " in rest:
                text_portion = rest.split(": ", 1)[1]

        bg = label_colors.get(label.lower(), "#ddd")
        color = "white" if label.lower() in light_text_labels else "inherit"

        html_parts.append(
            f"<span class='tree-line'>"
            f"<span style='color:#999'>{indent}</span>"
            f"<span class='tree-label' style='background:{bg};color:{color}'>"
            f"{html_mod.escape(label)}</span>"
        )
        if text_portion:
            html_parts.append(
                f"<span class='tree-text'>\"{html_mod.escape(text_portion[:80])}\"</span>"
            )
        html_parts.append("</span>")

    html_parts.extend(["</div>", "</body></html>"])

    return "\n".join(html_parts)
