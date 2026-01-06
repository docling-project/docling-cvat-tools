# docling-cvat-tools

CVAT annotation tools for Docling document processing and evaluation.

This package provides comprehensive tools for working with CVAT (Computer Vision Annotation Tool) annotations in the context of Docling document processing and evaluation workflows.

## Features

- **CVAT XML Parsing**: Parse and validate CVAT XML annotation files
- **Document Conversion**: Convert CVAT annotations to `DoclingDocument` format
- **Validation**: Validate CVAT annotations for correctness and completeness
- **Visualization**: Generate HTML visualizations of annotated documents
- **CLI Tools**: Command-line utilities for common CVAT workflows

## Installation

```bash
pip install docling-cvat-tools
```

Or install as an optional dependency of `docling-eval`:

```bash
pip install "docling-eval[campaign-tools]"
```


## Requirements

- Python >=3.10,<4.0
- docling-core (document types)
- docling (for document processing)

## Usage

### CLI Tools

#### Validate CVAT annotations

```bash
docling-cvat-validator path/to/annotations.xml
```

#### Convert CVAT to DoclingDocument

```bash
docling-cvat-to-docling --input_path path/to/cvat_folder --output-dir output/
```

### Python API

```python
from docling_cvat_tools.cvat_tools.parser import parse_cvat_file
from docling_cvat_tools.cvat_tools.cvat_to_docling import convert_cvat_to_docling
from docling_cvat_tools.cvat_tools.validator import validate_cvat_sample

# Parse CVAT XML file
parsed = parse_cvat_file(Path("annotations.xml"))

# Validate annotations
validation_result = validate_cvat_sample(
    xml_path=Path("annotations.xml"),
    image_filename="page_000001.png"
)

# Convert CVAT folder to DoclingDocuments
results = convert_cvat_to_docling(
    xml_path=Path("annotations.xml"),
    input_path=Path("document.pdf"),
    image_identifier="page_000001.png",
    output_dir=Path("output")
)
```

### Integration with docling-eval

This package is designed to work seamlessly with `docling-eval`. When installed as an optional dependency, it enables CVAT-specific features in the evaluation framework:

- CVAT dataset builders (`CvatDatasetBuilder`, `CvatPreannotationBuilder`)
- CVAT evaluation pipelines

## Package Structure

- `docling_cvat_tools.cvat_tools`: Core CVAT parsing, conversion, and validation
- `docling_cvat_tools.datamodels`: CVAT-specific data models
- `docling_cvat_tools.visualisation`: HTML visualization utilities
- `docling_cvat_tools.cli`: Command-line interface tools
- `docling_cvat_tools.utils`: Utility functions

## Development

```bash
# Install in development mode
uv sync

# Run tests
uv run pytest
```

## License

MIT
