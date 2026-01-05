"""Utility functions for CVAT tools."""

import base64
import io
import logging
from collections import defaultdict
from typing import List

from docling_core.types.doc.document import DoclingDocument, GraphData
from docling_core.types.doc.labels import GraphCellLabel
from PIL import Image

_logger = logging.getLogger(__name__)


def classify_cells(graph: GraphData) -> None:
    """
    For a graph consisting of a list of GraphCell objects (nodes) and a list of GraphLink objects
    (directed edges), update each cell's label according to the following rules:
      - If a node has no outgoing edges, label it as VALUE.
      - If a node has no incoming edges and has one or more outgoing edges, label it as KEY.
      - If a node has one or more incoming edges and one or more outgoing edges, but every neighbor it points to is a leaf (has no outgoing edges), label it as KEY.
      - Otherwise, label it as UNSPECIFIED.

    This function modifies the cells in place.
    """
    # for tracking the values
    indegree = defaultdict(int)
    outdegree = defaultdict(int)
    outgoing_neighbors: defaultdict[int, List[int]] = defaultdict(list)

    cells, links = graph.cells, graph.links

    # initialization
    for cell in cells:
        indegree[cell.cell_id] = 0
        outdegree[cell.cell_id] = 0
        outgoing_neighbors[cell.cell_id] = []

    # populate the values
    for link in links:
        src = link.source_cell_id
        tgt = link.target_cell_id
        outdegree[src] += 1
        indegree[tgt] += 1
        outgoing_neighbors[src].append(tgt)

    # now, we assign labels based on the computed degrees.
    for cell in cells:
        cid = cell.cell_id
        if outdegree[cid] == 0:
            # if a node is a leaf, it is a VALUE.
            cell.label = GraphCellLabel.VALUE
        elif indegree[cid] == 0:
            # no incoming and at least one outgoing means it is a KEY.
            cell.label = GraphCellLabel.KEY
        elif outdegree[cid] > 0 and indegree[cid] > 0:
            # if all outgoing neighbors are leaves (i.e. outdegree == 0),
            # then this node is a KEY.
            if all(outdegree[neighbor] == 0 for neighbor in outgoing_neighbors[cid]):
                cell.label = GraphCellLabel.KEY
            else:
                # otherwise, it is UNSPECIFIED.
                cell.label = GraphCellLabel.UNSPECIFIED
        else:
            # fallback case.
            cell.label = GraphCellLabel.UNSPECIFIED


def sort_cell_ids(doc: DoclingDocument) -> None:
    """Sort cell IDs in key-value items to be sequential."""
    if not doc.key_value_items:
        return
    mapping = {}
    for i, item in enumerate(doc.key_value_items[0].graph.cells):
        mapping[item.cell_id] = i
    for i, item in enumerate(doc.key_value_items[0].graph.cells):
        item.cell_id = mapping[item.cell_id]
    for i, link in enumerate(doc.key_value_items[0].graph.links):
        if link.source_cell_id in mapping:
            link.source_cell_id = mapping[link.source_cell_id]
        else:
            _logger.warning(
                f"Link {i} source_cell_id {link.source_cell_id} not found in mapping."
            )
        if link.target_cell_id in mapping:
            link.target_cell_id = mapping[link.target_cell_id]
        else:
            _logger.warning(
                f"Link {i} target_cell_id {link.target_cell_id} not found in mapping."
            )


def from_pil_to_base64(img: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    # Convert the image to a base64 str
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")  # Specify the format (e.g., JPEG, PNG, etc.)
    image_bytes = buffered.getvalue()

    # Encode the bytes to a Base64 string
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    return image_base64
