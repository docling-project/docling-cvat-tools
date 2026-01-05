from __future__ import annotations

from pathlib import Path

import pytest
from docling_core.types.doc.base import BoundingBox, CoordOrigin

from docling_cvat_tools.cvat_tools.geometry import bbox_enclosing_rotated_rect
from docling_cvat_tools.cvat_tools.parser import parse_cvat_file


def test_bbox_enclosing_rotated_rect_rotation_0_identity() -> None:
    bbox = BoundingBox(l=10.0, t=20.0, r=30.0, b=60.0, coord_origin=CoordOrigin.TOPLEFT)
    rotated = bbox_enclosing_rotated_rect(bbox, rotation_deg=0.0)
    assert rotated == bbox


def test_bbox_enclosing_rotated_rect_rotation_90_swaps_extents() -> None:
    # Center is (50, 50). Unrotated width=40 height=80 -> rotated AABB width=80 height=40.
    bbox = BoundingBox(l=30.0, t=10.0, r=70.0, b=90.0, coord_origin=CoordOrigin.TOPLEFT)
    rotated = bbox_enclosing_rotated_rect(bbox, rotation_deg=90.0)

    assert rotated.coord_origin == CoordOrigin.TOPLEFT
    assert rotated.l == pytest.approx(10.0)
    assert rotated.r == pytest.approx(90.0)
    assert rotated.t == pytest.approx(30.0)
    assert rotated.b == pytest.approx(70.0)


def test_parse_cvat_file_preserves_rotation_and_adjusts_bbox(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<annotations>
  <image id="1" name="page.png" width="100" height="100">
    <box label="text" source="" occluded="0" xtl="30" ytl="10" xbr="70" ybr="90" rotation="90.0" z_order="0">
      <attribute name="content_layer">BODY</attribute>
    </box>
  </image>
</annotations>
"""
    xml_path = tmp_path / "annotations.xml"
    xml_path.write_text(xml, encoding="utf-8")

    parsed = parse_cvat_file(xml_path)
    image = parsed.get_image("page.png")
    assert len(image.elements) == 1

    elem = image.elements[0]
    assert elem.rotation_deg == 90.0
    assert elem.bbox_unrotated is not None
    assert elem.bbox_unrotated == BoundingBox(
        l=30.0, t=10.0, r=70.0, b=90.0, coord_origin=CoordOrigin.TOPLEFT
    )

    # The stored bbox must be the enclosing axis-aligned bbox of the rotated rectangle.
    assert elem.bbox.coord_origin == CoordOrigin.TOPLEFT
    assert elem.bbox.l == pytest.approx(10.0)
    assert elem.bbox.t == pytest.approx(30.0)
    assert elem.bbox.r == pytest.approx(90.0)
    assert elem.bbox.b == pytest.approx(70.0)
