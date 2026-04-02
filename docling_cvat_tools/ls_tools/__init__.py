"""Label Studio to DoclingDocument conversion tools.

This package provides a deterministic conversion pipeline from Label Studio
annotations to DoclingDocument, using the connectedRegions field for explicit
element references instead of heuristic-based proximity matching.

Main entry point:
    convert_ls_to_docling(regions, image_width, image_height, ...)

Example:
    from docling_cvat_tools.ls_tools import convert_ls_to_docling

    result = convert_ls_to_docling(
        regions=annotation_regions,
        image_width=1000,
        image_height=1400,
        image_url="https://example.com/image.png",
        document_name="my_document",
    )

    print(result.html)
    print(result.docling_document.export_to_markdown())
"""

from .ls_to_docling import ConversionResult, convert_ls_to_docling
from .models import (
    ElementLabel,
    ListState,
    LSDocument,
    LSElement,
    LSPath,
    ResolvedPaths,
)

__all__ = [
    # Main entry point
    "convert_ls_to_docling",
    "ConversionResult",
    # Data models
    "LSElement",
    "LSPath",
    "LSDocument",
    "ResolvedPaths",
    "ListState",
    "ElementLabel",
]
