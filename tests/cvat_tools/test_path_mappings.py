"""Tests for path_mapping utilities."""

import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin
from docling_core.types.doc.document import ContentLayer
from docling_core.types.doc.labels import DocItemLabel

from docling_cvat_tools.cvat_tools.models import CVATAnnotationPath, CVATElement
from docling_cvat_tools.cvat_tools.path_mappings import (
    PathMappings,
    _resolve_reading_order_conflicts,
    promote_table_cross_boundary_reading_order,
)
from docling_cvat_tools.cvat_tools.tree import TreeNode, build_global_reading_order


def _table_elements() -> tuple[list[TreeNode], CVATElement, CVATElement]:
    table_element = CVATElement(
        id=1,
        label=DocItemLabel.TABLE,
        bbox=BoundingBox(
            l=0.0, t=0.0, r=100.0, b=100.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )
    cell_element = CVATElement(
        id=2,
        label=DocItemLabel.LIST_ITEM,
        bbox=BoundingBox(
            l=10.0, t=10.0, r=90.0, b=40.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )

    table_node = TreeNode(element=table_element)
    cell_node = TreeNode(element=cell_element)
    table_node.add_child(cell_node)

    return [table_node], table_element, cell_element


def test_promote_table_cross_boundary_inserts_table_before_descendants() -> None:
    tree_roots, table_element, cell_element = _table_elements()

    path = CVATAnnotationPath(
        id=10,
        label="reading_order",
        points=[(50.0, -10.0), (50.0, 50.0)],
    )

    mappings = PathMappings(
        reading_order={path.id: [cell_element.id]},
        merge={},
        group={},
        to_caption={},
        to_footnote={},
        to_value={},
    )

    promote_table_cross_boundary_reading_order(
        mappings, [path], tree_roots, tolerance=0.0
    )

    assert mappings.reading_order[path.id] == [
        table_element.id,
        cell_element.id,
    ]


def test_promote_table_cross_boundary_ignores_paths_fully_inside() -> None:
    tree_roots, table_element, cell_element = _table_elements()

    path = CVATAnnotationPath(
        id=11,
        label="reading_order",
        points=[(20.0, 20.0), (80.0, 30.0)],
    )

    mappings = PathMappings(
        reading_order={path.id: [cell_element.id]},
        merge={},
        group={},
        to_caption={},
        to_footnote={},
        to_value={},
    )

    promote_table_cross_boundary_reading_order(
        mappings, [path], tree_roots, tolerance=0.0
    )

    assert mappings.reading_order[path.id] == [cell_element.id]


def test_conflict_resolution_reinserts_container_at_original_index() -> None:
    table_bbox = BoundingBox(
        l=0.0, t=0.0, r=100.0, b=100.0, coord_origin=CoordOrigin.TOPLEFT
    )
    table_element = CVATElement(
        id=50,
        label=DocItemLabel.TABLE,
        bbox=table_bbox,
        content_layer=ContentLayer.BODY,
    )
    cell_element = CVATElement(
        id=51,
        label=DocItemLabel.LIST_ITEM,
        bbox=BoundingBox(
            l=10.0, t=10.0, r=40.0, b=40.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )
    later_element = CVATElement(
        id=52,
        label=DocItemLabel.TEXT,
        bbox=BoundingBox(
            l=0.0, t=150.0, r=100.0, b=180.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )

    path_level1 = CVATAnnotationPath(
        id=100,
        label="reading_order",
        points=[(20.0, 20.0), (20.0, 160.0)],
        level=1,
    )
    path_level2 = CVATAnnotationPath(
        id=101,
        label="reading_order",
        points=[(20.0, 20.0), (30.0, 25.0)],
        level=2,
    )

    reading_order = {
        path_level1.id: [cell_element.id, later_element.id],
        path_level2.id: [cell_element.id],
    }

    updated = _resolve_reading_order_conflicts(
        reading_order,
        [path_level1, path_level2],
        [table_element, cell_element, later_element],
    )

    assert updated[path_level1.id] == [table_element.id, later_element.id]
    assert cell_element.id not in updated[path_level1.id]


def test_global_order_preserves_heading_before_text_when_path_says_so() -> None:
    text_element = CVATElement(
        id=200,
        label=DocItemLabel.TEXT,
        bbox=BoundingBox(
            l=0.0, t=0.0, r=100.0, b=40.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )
    heading_element = CVATElement(
        id=201,
        label=DocItemLabel.SECTION_HEADER,
        bbox=BoundingBox(
            l=10.0, t=5.0, r=90.0, b=20.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )

    text_node = TreeNode(element=text_element)
    heading_node = TreeNode(element=heading_element)
    text_node.add_child(heading_node)

    path = CVATAnnotationPath(
        id=300,
        label="reading_order",
        points=[(15.0, 10.0), (15.0, 30.0)],
        level=1,
    )

    paths = [path]
    path_to_elements = {path.id: [heading_element.id, text_element.id]}
    order = build_global_reading_order(
        paths,
        path_to_elements,
        path_to_container={},
        tree_roots=[text_node],
    )

    assert order[:2] == [heading_element.id, text_element.id]


def test_global_order_places_parent_after_heading_when_parent_absent_from_path() -> (
    None
):
    outer_text = CVATElement(
        id=300,
        label=DocItemLabel.TEXT,
        bbox=BoundingBox(
            l=0.0, t=0.0, r=200.0, b=80.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )
    heading_element = CVATElement(
        id=301,
        label=DocItemLabel.SECTION_HEADER,
        bbox=BoundingBox(
            l=10.0, t=10.0, r=150.0, b=40.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )
    following_text = CVATElement(
        id=302,
        label=DocItemLabel.TEXT,
        bbox=BoundingBox(
            l=0.0, t=100.0, r=200.0, b=180.0, coord_origin=CoordOrigin.TOPLEFT
        ),
        content_layer=ContentLayer.BODY,
    )

    outer_node = TreeNode(element=outer_text)
    heading_node = TreeNode(element=heading_element)
    outer_node.add_child(heading_node)

    following_node = TreeNode(element=following_text)

    path = CVATAnnotationPath(
        id=400,
        label="reading_order",
        points=[(20.0, 20.0), (20.0, 140.0)],
        level=1,
    )

    paths = [path]
    path_to_elements = {path.id: [heading_element.id, following_text.id]}
    order = build_global_reading_order(
        paths,
        path_to_elements,
        path_to_container={},
        tree_roots=[outer_node, following_node],
    )

    assert order[:3] == [
        heading_element.id,
        outer_text.id,
        following_text.id,
    ]
