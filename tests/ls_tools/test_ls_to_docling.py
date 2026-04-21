from docling_core.types.doc.labels import GraphLinkLabel

from docling_cvat_tools.ls_tools import convert_ls_to_docling


def _rect(
    region_id: str,
    label: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str = "",
    level=None,
    parent_id=None,
):
    region = {
        "id": region_id,
        "type": "rectanglelabels",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "rotation": 0,
        "rectanglelabels": [label],
        "content_layer": "BODY",
        "picture_type": None,
        "text": text,
    }
    if level is not None or "list_item" in label or "section_header" in label:
        region["level"] = level
    if parent_id is not None:
        region["parent_id"] = parent_id
    return region


def _picture(
    region_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    parent_id=None,
):
    region = _rect(
        region_id=region_id,
        label="picture",
        x=x,
        y=y,
        width=width,
        height=height,
        text="",
        parent_id=parent_id,
    )
    region["picture_type"] = "OTHER"
    return region


def _path(path_id: str, label: str, connected_regions: list[str]):
    return {
        "id": path_id,
        "type": "polylinelabels",
        "connectedRegions": connected_regions,
        "points": [[0, 0], [1, 1]],
        "polylinelabels": [label],
    }


def test_parent_id_children_and_key_value_regions_are_preserved() -> None:
    regions = [
        _picture("pic", 5, 5, 30, 30),
        _rect(
            "fake_text",
            "text",
            10,
            10,
            10,
            5,
            text="fake text",
            parent_id="pic",
        ),
        _rect("kv_parent", "text", 50, 5, 30, 12, text="Name: Alice"),
        _rect(
            "key_1",
            "key",
            51,
            6,
            10,
            4,
            text="Name",
            parent_id="kv_parent",
        ),
        _rect(
            "value_1",
            "value",
            63,
            6,
            14,
            4,
            text="Alice",
            parent_id="kv_parent",
        ),
        _path("reading", "reading_order", ["pic", "kv_parent"]),
        _path("kv_link", "to_value", ["key_1", "value_1"]),
    ]

    result = convert_ls_to_docling(
        regions=regions,
        image_width=1000,
        image_height=1000,
        document_name="parent-id-test",
    )
    assert result.docling_document is not None
    doc = result.docling_document

    fake_text_item = next(item for item in doc.texts if item.text == "fake text")
    fake_text_parent = fake_text_item.parent
    assert fake_text_parent is not None
    assert fake_text_parent.cref == doc.pictures[0].self_ref

    assert len(doc.key_value_items) == 1
    graph = doc.key_value_items[0].graph
    assert [cell.text for cell in graph.cells] == ["Name", "Alice"]
    assert len(graph.links) == 1
    assert graph.links[0].label == GraphLinkLabel.TO_VALUE
    first_cell_item_ref = graph.cells[0].item_ref
    second_cell_item_ref = graph.cells[1].item_ref
    assert first_cell_item_ref is not None
    assert second_cell_item_ref is not None
    assert first_cell_item_ref.cref == doc.texts[0].self_ref
    assert second_cell_item_ref.cref == doc.texts[0].self_ref


def test_grouped_lists_respect_inferred_levels_and_nested_sublists() -> None:
    regions = [
        _rect("li1", "list_item", 5, 5, 20, 5, text="One", level=1),
        _rect("li2", "list_item", 5, 12, 20, 5, text="Two", level=None),
        _rect("li3", "list_item", 8, 19, 20, 5, text="Two.One", level=2),
        _rect("li4", "list_item", 8, 26, 20, 5, text="Two.Two", level=None),
        _rect("li5", "list_item", 5, 33, 20, 5, text="Three", level=1),
        _path("reading", "reading_order", ["li1", "li2", "li3", "li4", "li5"]),
        _path("group_1", "group", ["li1", "li2", "li3", "li4", "li5"]),
    ]

    result = convert_ls_to_docling(
        regions=regions,
        image_width=1000,
        image_height=1000,
        document_name="nested-list-test",
    )
    assert result.docling_document is not None
    doc = result.docling_document

    assert len(doc.groups) == 2
    top_group, sub_group = doc.groups
    assert [ref.cref for ref in top_group.children] == [
        doc.texts[0].self_ref,
        doc.texts[1].self_ref,
        doc.texts[4].self_ref,
    ]
    assert [ref.cref for ref in sub_group.children] == [
        doc.texts[2].self_ref,
        doc.texts[3].self_ref,
    ]
    sub_group_parent = sub_group.parent
    assert sub_group_parent is not None
    assert sub_group_parent.cref == doc.texts[1].self_ref

    first_parent = doc.texts[0].parent
    second_parent = doc.texts[1].parent
    third_parent = doc.texts[2].parent
    fourth_parent = doc.texts[3].parent
    fifth_parent = doc.texts[4].parent
    assert first_parent is not None
    assert second_parent is not None
    assert third_parent is not None
    assert fourth_parent is not None
    assert fifth_parent is not None
    assert first_parent.cref == top_group.self_ref
    assert second_parent.cref == top_group.self_ref
    assert third_parent.cref == sub_group.self_ref
    assert fourth_parent.cref == sub_group.self_ref
    assert fifth_parent.cref == top_group.self_ref


def test_section_header_level_is_inferred_from_previous_explicit_level() -> None:
    regions = [
        _rect("h1", "section_header", 5, 5, 30, 5, text="A", level=3),
        _rect("h2", "section_header", 5, 12, 30, 5, text="B", level=None),
        _path("reading", "reading_order", ["h1", "h2"]),
    ]

    result = convert_ls_to_docling(
        regions=regions,
        image_width=1000,
        image_height=1000,
        document_name="heading-level-test",
    )
    assert result.docling_document is not None
    doc = result.docling_document

    assert [getattr(item, "level") for item in doc.texts] == [3, 3]
