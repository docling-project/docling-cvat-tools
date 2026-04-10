"""Regression tests for CVAT to DoclingDocument conversion."""

import csv
import os
import sys
from pathlib import Path
from typing import Optional

import pytest
from docling_core.types.doc import DoclingDocument, ImageRefMode
from docling_core.types.doc.document import ContentLayer
from pydantic import BaseModel, ValidationError

from docling_cvat_tools.cvat_tools.cvat_to_docling import convert_cvat_to_docling
from docling_cvat_tools.visualisation.visualisations import save_single_document_html

IS_CI = bool(os.getenv("CI"))


class FixtureMeta(BaseModel):
    name: str
    category: str
    description: str
    observation_status: str
    observation: Optional[str] = None
    input_type: Optional[str] = None
    page_number: Optional[int] = None
    image_identifier: str
    force_ocr: Optional[bool] = None
    ocr_scale: Optional[float] = None
    cvat_input_scale: Optional[float] = None
    storage_scale: Optional[float] = None


def strip_image_uris(d):
    """Strip image URIs from dict for platform-independent comparison.

    Adopted from docling-core tests - images are platform-dependent due to
    rendering differences (fonts, anti-aliasing, etc.) between macOS and Linux.
    """
    if isinstance(d, dict):
        return {
            k: strip_image_uris(v)
            for k, v in d.items()
            if k not in {"uri", "image_uri"}
        }
    elif isinstance(d, list):
        return [strip_image_uris(x) for x in d]
    else:
        return d


def load_all_metadata() -> dict[str, FixtureMeta]:
    """Load all test metadata from unified CSV file."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    csv_path = fixtures_dir / "metadata.csv"

    if not csv_path.exists():
        return {}

    metadata_by_folder: dict[str, FixtureMeta] = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            folder_name = row["name"]
            metadata_by_folder[folder_name] = FixtureMeta(
                name=folder_name,
                category=row["category"],
                description=row["description"],
                observation_status=row["observation_status"],
                observation=row.get("observation"),
                input_type=row.get("input_type"),
                page_number=(
                    int(page_num) if (page_num := row.get("page_number")) else None
                ),
                image_identifier=row["source_image_identifier"],
            )
    return metadata_by_folder


# Load all metadata once at module level
ALL_METADATA = load_all_metadata()


def discover_fixtures() -> list[Path]:
    """Discover all test fixtures."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    if not fixtures_dir.exists():
        return []

    fixtures = []
    for folder_name in sorted(ALL_METADATA.keys()):
        fixture_dir = fixtures_dir / folder_name
        if fixture_dir.is_dir() and not folder_name.startswith("_"):
            fixtures.append(fixture_dir)

    return fixtures


# Discover all fixtures for parametrization
FIXTURES = discover_fixtures()
FIXTURE_IDS = [f.name for f in FIXTURES]

# Check if we're in generation mode
GENERATE_MODE = os.environ.get("DOCLING_GEN_TEST_DATA", "").lower() in (
    "1",
    "true",
    "yes",
)

# Check if we should generate visualizations
GENERATE_VIZ = os.environ.get("DOCLING_GEN_VIZ", "").lower() in ("1", "true", "yes")

# Visualization output directory
VIZ_OUTPUT_DIR = Path(__file__).parent.parent.parent / "scratch" / "cvat_regression_viz"


def _check_validation(doc: DoclingDocument):
    DoclingDocument.model_validate(doc)


def _check_html_ser(doc: DoclingDocument):
    doc.export_to_html(
        image_mode=ImageRefMode.EMBEDDED,
        split_page_view=True,
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
    )


def _xfail(code: str, msg: str):
    pytest.xfail(f"Expected failure[{code}]: {msg}")


@pytest.mark.skipif(
    IS_CI, reason="Skipping test in CI because cvat_to_docling only runs on macOS."
)
@pytest.mark.parametrize("fixture_dir", FIXTURES, ids=FIXTURE_IDS)
def test_cvat_to_docling_regression(fixture_dir: Path) -> None:
    """Test CVAT to DoclingDocument conversion against expected output."""
    # Load test metadata from unified CSV
    metadata = ALL_METADATA[fixture_dir.name]

    # Get observation status and value
    observation_status = metadata.observation_status
    observation = metadata.observation or "No observation recorded"

    # Input paths - check for PDF first, then PNG
    xml_path = fixture_dir / "input.xml"
    pdf_path = fixture_dir / "input.pdf"
    png_path = fixture_dir / "input.png"

    assert xml_path.exists(), f"Missing input.xml in {fixture_dir}"

    # Determine input type and path
    if pdf_path.exists():
        input_path = pdf_path
        input_type = "pdf"
        page_number = metadata.page_number or 1
    elif png_path.exists():
        input_path = png_path
        input_type = "png"
        page_number = None
    else:
        pytest.fail(f"Missing input file (input.pdf or input.png) in {fixture_dir}")

    # TODO: PNG inputs require OCR which uses ocrmac (macOS-only).
    # On Linux CI, ocrmac is not available.
    # Potentially better solutions:
    #   1. Pre-generate OCR results and store in fixtures
    #   3. Mock OCR with cached results for non-macOS platforms
    # For now, skip PNG-based tests on non-macOS platforms.
    if input_type == "png" and sys.platform != "darwin":
        pytest.skip(
            f"Test {fixture_dir.name} requires OCR (PNG input) which is only available on macOS"
        )

    # Set defaults based on input type
    # PDF: cvat_input_scale=2.0, storage_scale=2.0 (144 DPI)
    # PNG: cvat_input_scale=1.0, storage_scale=1.0 (72 DPI)
    if input_type == "pdf":
        default_cvat_input_scale = 2.0
        default_storage_scale = 2.0
    else:
        default_cvat_input_scale = 1.0
        default_storage_scale = 1.0

    # conversion parameters
    force_ocr = metadata.force_ocr if metadata.force_ocr is not None else False
    ocr_scale = metadata.ocr_scale if metadata.ocr_scale is not None else 3.0
    cvat_input_scale = (
        metadata.cvat_input_scale
        if metadata.cvat_input_scale is not None
        else default_cvat_input_scale
    )
    storage_scale = (
        metadata.storage_scale
        if metadata.storage_scale is not None
        else default_storage_scale
    )

    # Get image identifier from metadata
    image_identifier = metadata.image_identifier

    # Perform conversion
    actual_doc: Optional[DoclingDocument] = convert_cvat_to_docling(
        xml_path=xml_path,
        input_path=input_path,
        image_identifier=image_identifier,
        force_ocr=force_ocr,
        ocr_scale=ocr_scale,
        cvat_input_scale=cvat_input_scale,
        storage_scale=storage_scale,
    )

    assert actual_doc is not None, f"Conversion failed for {fixture_dir.name}"

    if observation_status == "broken_validation_ref_dupl":
        with pytest.raises(ValidationError) as valid_err_info:
            _check_validation(actual_doc)
        error_str = str(valid_err_info.value)
        assert "Duplicate ref" in error_str
        _xfail(code=observation_status, msg=observation)

    actual_doc._normalize_table_children_from_rich_cells()

    if observation_status == "broken_validation":
        with pytest.raises(ValidationError) as valid_err_info:
            _check_validation(actual_doc)
        error_str = str(valid_err_info.value)
        assert "Document hierarchy is inconsistent." in error_str
        _xfail(code=observation_status, msg=observation)

    _check_validation(actual_doc)

    if observation_status == "broken_manifested_in_html_ser":
        with pytest.raises(ValueError) as value_err_info:
            _check_html_ser(actual_doc)
        error_str = str(value_err_info.value)
        # assert "Coordinate 'right' is less than 'left'" in error_str
        _xfail(code=observation_status, msg=observation)

    _check_html_ser(actual_doc)

    # Generate visualizations if requested
    if GENERATE_VIZ or GENERATE_MODE:
        viz_dir = VIZ_OUTPUT_DIR / fixture_dir.name
        viz_dir.mkdir(parents=True, exist_ok=True)

        # Save HTML visualization
        actual_doc.save_as_html(
            viz_dir / "output.html",
            image_mode=ImageRefMode.EMBEDDED,
            split_page_view=True,
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
        )

        # Save JSON for inspection
        actual_doc.save_as_json(viz_dir / "output.json")

        # Save visualizations with reading order (same as test_cvat_to_docling_cli.py)
        # This generates:
        #   - visualization_layout.html (layout with reading order overlay)
        #   - visualization_key_value.html (if key-value items exist)
        visualization_path = viz_dir / "visualization.html"
        save_single_document_html(
            visualization_path, actual_doc, draw_reading_order=True
        )

    post_migr = actual_doc.model_copy(deep=True)

    if observation_status == "migration_issue":
        with pytest.raises(ValueError) as value_err_info:
            post_migr._migrate_to_field_regions()
        assert "_migrate_to_field_regions" in [
            frame.name for frame in value_err_info.traceback
        ]
        _xfail(code=observation_status, msg=observation)
    else:
        post_migr._migrate_to_field_regions()

    dclg_txt = actual_doc.export_to_doclang()
    exp_path = fixture_dir / "expected.json"
    exp_post_migr_path = fixture_dir / "expected_post_migr.json"
    exp_dclg_path = fixture_dir / "expected.dclg.xml"

    if GENERATE_MODE:
        # Generate expected output
        actual_doc.save_as_json(exp_path)
        post_migr.save_as_json(exp_post_migr_path)
        with open(exp_dclg_path, "w") as f:
            f.write(f"{dclg_txt}\n")
        pytest.skip(f"Generated expected output for {fixture_dir.name}")
    else:
        # Compare with expected output
        if not exp_path.exists():
            pytest.fail(
                f"Missing {exp_path}. "
                f"Run with DOCLING_GEN_TEST_DATA=1 to generate it."
            )

        expected_doc = DoclingDocument.load_from_json(exp_path)
        exp_post_migr_doc = DoclingDocument.load_from_json(exp_post_migr_path)

        # Serialize and deserialize actual_doc to match the behavior of expected_doc
        # This ensures both go through the same serialization cycle
        actual_doc_json = actual_doc.export_to_dict()
        actual_doc = DoclingDocument.model_validate(actual_doc_json)

        # Normalize references before comparison for deterministic equality
        actual_doc._normalize_references()
        expected_doc._normalize_references()

        # Compare using stripped dicts (ignore image URIs - platform-dependent rendering)
        # Following docling-core test pattern: "test was flaky due to URIs"
        actual_stripped = strip_image_uris(actual_doc.export_to_dict())
        expected_stripped = strip_image_uris(expected_doc.export_to_dict())
        matches = actual_stripped == expected_stripped

        # Handle broken tests
        if matches and observation_status == "broken":
            # Reproduced the known broken behavior - expected failure
            pytest.xfail(f"Expected failure[{observation_status}]: {observation}")

        if not matches and observation_status == "broken":
            # Output differs from the known broken expected.json - unexpected
            pytest.fail(
                f"Test {fixture_dir.name} is marked as 'broken' but produced unexpected output "
                f"(differs from the known broken expected.json)."
            )

        # For correct/unknown tests, assert equality (will fail if not matches)
        assert matches, (
            f"Conversion output differs from expected for {fixture_dir.name}. "
            f"Test category: {metadata.category}, Description: {metadata.description}"
        )

        assert post_migr.export_to_dict() == exp_post_migr_doc.export_to_dict()

        with open(exp_dclg_path) as f:
            expected_dclg_xml = f.read()
        assert dclg_txt.strip() == expected_dclg_xml.strip()


def test_fixtures_exist() -> None:
    """Sanity check that we have fixtures to test."""
    assert (
        len(FIXTURES) > 0
    ), "No test fixtures found. Run tests/cvat_to_docling/build_fixtures.py to create fixtures."


if __name__ == "__main__":
    # Allow running this file directly for debugging
    pytest.main([__file__, "-v"])
