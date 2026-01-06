# CVAT to DoclingDocument Conversion Reference

This document describes the fundamental principles and rules governing how a DoclingDocument is constructed from CVAT XML annotations.

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Stage 1: Parsing CVAT XML](#stage-1-parsing-cvat-xml)
3. [Stage 2: Building DocumentStructure](#stage-2-building-documentstructure)
4. [Stage 3: Reading Order Processing](#stage-3-reading-order-processing)
5. [Stage 4: DoclingDocument Conversion](#stage-4-doclingdocument-conversion)
6. [Path Types and Their Behavior](#path-types-and-their-behavior)
7. [Special Cases and Exceptions](#special-cases-and-exceptions)
8. [Known Issues and Edge Cases](#known-issues-and-edge-cases)

---

## Pipeline Overview

```
CVAT XML → ParsedCVATImage → DocumentStructure → DoclingDocument
           (raw elements)     (tree + mappings)    (final output)
```

The conversion happens in four distinct stages:

1. **Parsing**: Extract elements (`<box>`) and paths (`<polyline>`) from XML
2. **Structure Building**: Construct containment tree and map paths to elements
3. **Reading Order**: Build global reading order and reorder tree
4. **Conversion**: Create DoclingDocument items with proper hierarchy

---

## Stage 1: Parsing CVAT XML

### Element Types (`<box>`)

Each `<box>` element becomes a `CVATElement` with:

| Field | Description |
|-------|-------------|
| `label` | Element type (DocItemLabel, GraphCellLabel, or TableStructLabel) |
| `bbox` | Bounding box (TOPLEFT origin, pixel coordinates) |
| `content_layer` | BODY, FURNITURE, or BACKGROUND |
| `level` | Hierarchy level (for list items, sections) |
| `type` | Picture classification (CHART, LOGO, ILLUSTRATION, etc.) |
| `rotation_deg` | Rotation angle (axis-aligned enclosing bbox computed) |

### Label Recognition Priority

Labels are resolved in this precedence:
1. Apply manual mappings (e.g. `fillable_field` → `DocItemLabel.EMPTY_VALUE`)
2. Try to parse as `DocItemLabel` (text, picture, table, list_item, etc.)
3. Try to parse as `GraphCellLabel` (key, value)
4. Try to parse as `TableStructLabel` (table_row, table_column, table_merged_cell, col_header, row_header, row_section)

### Path Types (`<polyline>`)

| Label | Purpose |
|-------|---------|
| `reading_order` | Defines element order (level=1 for global, level=2+ for nested) |
| `merge` | Combines multiple elements into one logical item (multi-provenance in DocItem) |
| `group` | Groups related elements (e.g., list items) |
| `to_caption` | Links container to its caption |
| `to_footnote` | Links container to its footnote |
| `to_value` | Links key element to value element |

---

## Stage 2: Building DocumentStructure

### Containment Tree Construction

The containment tree determines parent-child relationships based on spatial containment:

```
For each element E:
    Find the smallest element P where:
        1. P.content_layer == E.content_layer
        2. P.bbox.area() > E.bbox.area()
        3. bbox_fraction_inside(E.bbox, P.bbox) > 0.7 (70% threshold)
    
    If P found: P becomes E's parent
    Otherwise: E is a root node
```

**Key constraint**: Containment is scoped by `content_layer`. A BODY element cannot be contained by a FURNITURE element.

### Path-to-Element Mapping

Polyline control points are mapped to elements by finding the **deepest** (smallest-area) element whose bbox contains the point (within some pixel tolerance).

**Critical behavior**: The path hits the innermost element, not its containers.

```
Example: Picture P contains Text T
         Reading order point lands on T's bbox
         
Result: T is hit, P is NOT automatically included
        To include P, the polyline must explicitly touch P's bbox
        (but not any of its children)
```

**Comment**: This pattern makes things fragile. For example, arbitrary child elements of a table or picture may be hit by a global reading-order path which was supposed to hit the table itself. This is later fixed through multiple heuristics, including:

- the ancestor insertion rules (see below), which ensures that parents of elements in reading order are inserted before, even if not hit by a path control point themselves.
- reading-order conflict resolution (further below), which ensures elements that are hit by multiple reading-order paths are assigned to the deepest reading-order.

Therefore, the code has become messy and brittle for complex scenarios.


### Path Mapping Filters

Different path types filter elements differently.

- `reading_order`, `group` paths should only contain elements which have a `DocItemLabel`. Pure Key or Value elements (GraphCellLabel) can not be grouped or put in reading-order directly, this is defined by the parent element that owns them. 
- `merge` paths should be allowed to merge `DocItemLabel` or `GraphCellLabel` elements. **Comment**: Currently, `GraphCellLabel` elements (Key-value) are excluded from merge path processing, and handled outside the path mappings in the `cvat_to_docling` conversion only (very ugly!).
- `to_caption`, `to_footnote` paths should only allow linking a container (table, picture, code, form) to a caption-label element or footnote-label element. The direction of the relationship should be auto-corrected based on the labels of the elements. **Comment**: Currently, the tolerance of reverse direction is handled in the `path_mappings` module, but then the `cvat_to_docling` module applies label-correction rules again, which is redundant and fragile.

No paths must ever include elements with `TableStructLabel` labels, since these are exclusively for explaining table structure and do not participate in reading-order or any other grouping.


---

## Stage 3: Reading Order Processing

### Level-1 vs Level-2+ Reading Order

- **Level-1**: Global page reading order (exactly one required per page)
- **Level-2+**: Nested reading order within containers (e.g., table cells, pictures)

### Global Reading Order Algorithm

```
1. Find all level-1 reading order paths
2. For each element in path order:
   a. Insert any unvisited CONTAINER ancestors (outermost first).
   b. Insert the element itself
   c. Insert any unvisited NON-CONTAINER ancestors (innermost to outermost)
   
3. When a container with level-2+ path is encountered:
   Process its nested reading order recursively
```

**Comment**: The distinction for container and non-container ancestors has been established because of scenarios seen in real-world cases, but bears complexities that often break proper conversion with real-world CVAT annotations. 

Container-type ancestors (table, picture, code, form) are the regular case, such as a picture containing child texts. If the reading order path goes straight to the text inside a picture, the picture container is automatically inserted first in reading order without being hit explicitly by a reading-order control point. This is well aligned because reading-order then follows the containment hierarchy, i.e. traversal order of the document tree.

Non-container ancestors appear in different situations. It is for example seen when a text paragraph has an inlined section-header. The section header is _contained_ in the text paragraph but should not be a _child_ of the text, and not follow but preceede the text in reading order. In a reading-order path where the inlined section-header is hit first, and the including text paragraph is hit second, the containment-based insertion must not trigger like with pictures, tables, otherwise the text will be moved to the first position. Other cases include key-value elements inside list-items, fillable fields in any other elements, and more. The handling is often ambiguous and conflicts with the deepest-element-first algorithm for path mappings.
The current code doesn't distinguish between these different non-container scenarios - it applies the same "insert after" rule blindly. The examples given (section-header vs key-value) have very different semantics but get identical treatment.
Fundamental reconsiderations are required here. 


### Ancestor Insertion Rules

| Ancestor Type | Insertion Position |
|--------------|-------------------|
| Container (table, picture, form, code) | BEFORE descendants |
| Non-container (text, section_header) | AFTER the descendant that triggers inclusion |

### Conflict Resolution

When an element appears in multiple reading order paths:

```
Rule: Assign element to the deepest-level path (highest level number wins)

Example:
    Element E in level-1 path P1 and level-2 path P2
    Result: E is removed from P1, kept only in P2
    
Compensation: If P1 loses elements, insert the smallest enclosing container
              at the removal position (if path crosses container boundary)
```

**Comment**: As introduced above, this handling is necessary because control points of multiple reading-order paths may hit the same element. This may be correct if one of the reading-order paths is relating to a parent element while hitting a child element inside. 
However, the "smallest enclosing container" logic (see _find_container_for_conflicted_path() in path_mappings.py) is complex and fragile. It groups lost elements by container, but if the container itself is crossed by multiple paths, or if lost elements have different containers, the heuristic may insert containers in wrong positions or miss some entirely.

---

## Stage 4: DoclingDocument Conversion

**Comment**: A couple of compromises are made in the CVAT to Docling conversion which are mostly rooted in constraints of the DoclingDocument format to express valid document structure that CVAT annotations cannot strictly impose. This reflects in a number of ad-hoc heuristics in the transformation code that makes it hard to debug and partially overrides or replaces rules applied in the earlier CVAT parsing stage. Refactoring for separation of concerns must be strongly considered. 

### Processing Order

1. Add pages from SegmentedPages
2. Apply reading order to containment tree
3. Process elements in global reading order
4. Process table data (compute cell grid)
5. Process caption/footnote relationships
6. Process to_value relationships (build GraphData)
7. Prune empty groups
8. Scale coordinates to storage scale

**Comment**: Several processing steps attempt fixes on already created DocItems, which is dangerous. Caption/footnote processing happens for example AFTER all elements are already created and placed in the tree (step 3). This means if a caption was incorrectly processed as a regular text element in step 3, it gets "fixed" by creating a new caption item in step 5, but the original text item may remain in the tree. The cleanup for this duplication isn't explicit.

### Element-to-DocItem Mapping

| CVAT Label | DoclingDocument Method |
|------------|----------------------|
| `TITLE` | `doc.add_title()` |
| `SECTION_HEADER` | `doc.add_heading(level=element.level)` |
| `TEXT`, `CAPTION`, `FOOTNOTE` | `doc.add_text(label=...)` |
| `LIST_ITEM` | `doc.add_list_item()` with hierarchy |
| `TABLE`, `DOCUMENT_INDEX` | `doc.add_table()` with TableData |
| `PICTURE`, `HANDWRITTEN_TEXT` | `doc.add_picture()` |
| `CODE` | `doc.add_code()` |
| `FORM` | `doc.add_form()` |
| `FORMULA` | `doc.add_formula()` |

### List Hierarchy

List items use a level-based nesting system:

```
level=1 → Parent is a LIST group container
level=N (N>1) → Parent is a sublist under level N-1 item

Level stack tracks most recent item at each level.
Higher levels cleared when lower level processed.
```

**Group association**:
- List items in a `group` path share a common LIST container
- Standalone list items get individual LIST containers

**Comment**: Logical list construction with hierarchy is complex and not handled in plain CVAT parsing, but only at DoclingDocument construction time. There are many failure modes related to list levels, list-level grouping and real-world annotation often abuse list-item elements in non-list contexts, which must be handled.

---

## Path Types and Their Behavior

### `reading_order` Paths

**Requirements**:
- Level-1: Exactly one per page (FATAL validation error if missing or multiple)
- Level-2+: Associated with containing element (table, picture, etc.)

**Validation**:
- All non-background elements should be touched by some reading order path
- Table children must be in level-2+ paths (level-1 insufficient)

### `merge` Paths

**Purpose**: Combine multiple elements into a single logical DocItem with multiple provenance instances.

**Constraints**:
- All elements must have same label (except checkbox mixed types)
- All elements must have same content_layer
- Cannot merge container elements (would destroy structure) - **Comment**: We do want to support merged tables, which is currently ignored.
- Cannot merge across different container boundaries - **Comment**: This is intentional since merges cannot span contexts across different tables, pictures or other containers.

**Direction correction**: If merge direction contradicts reading order, auto-correct and log warning.

### `group` Paths

**Purpose**: Associate related elements under a common container.

**Group labels determined by content**:
- All elements are pictures → `GroupLabel.PICTURE_AREA`
- All elements are list items → `GroupLabel.LIST`
- Contains checkboxes → `GroupLabel.FORM_AREA`
- Otherwise → `GroupLabel.UNSPECIFIED`

**Comment**: The heuristics regarding FORM_AREA are a poor-man approach.

### `to_caption` and `to_footnote` Paths

**Requirements**:
- Exactly 2 elements touched
- One must be a container (table, picture, form, code)
- Other is caption/footnote text

**Tolerance**:
- Auto-corrects reversed direction (caption → container becomes container → caption) - **Comment**: Redundant to CVAT parsing.
- Accepts TEXT-labeled elements and creates proper CAPTION/FOOTNOTE labels - **Comment**: This could be better handled during CVAT parsing to reduce redundancy.

**Failure modes**:
- Neither element is container → ERROR, path skipped
- May fail when the path hits a child element instead of the container itself, particularly when both endpoints are nested elements rather than the intended container.

### `to_value` Paths

**Requirements**:
- Exactly 2 logical elements (after merge resolution)
- Elements should be GraphCellLabel (key/value)

**Merge group handling**:
```
Path touches elements [A, B, C, D]
If A and B are in merge group M1, C and D in merge group M2:
    Resolves to 2 logical elements → valid

If resolved to != 2 logical groups → ERROR, path skipped
```

**Comment**: Both the key and the value may be composed of more than one CVAT element in case there are merges linking them. Hence this needs to be handled, but `GraphCell` elements only support a single provenance so far.

---

## Special Cases and Exceptions

### Elements Exempt from Reading Order Validation

| Element Type | Condition |
|--------------|-----------|
| `GraphCellLabel` (key/value) | Always exempt |
| `TableStructLabel` (structural) | Always exempt |
| Checkbox/Picture inside table | Always exempt |
| Elements in CHART/INFOGRAPHIC/ILLUSTRATION | Only if picture is completely untouched |
| Picture with descendants in level-1 | Exempt (descendants carry the order) |

### Table Cross-Boundary Reading Order

When a reading order path crosses a table boundary (points both inside and outside):

```
→ Table element itself is inserted into reading order
→ Preserves table's position while keeping descendant order
```

**Comment**: This is another reading-order fix-up, complementing the ancestor resolution and multiple-reading-order conflict resolution, and happens way too late in the DoclingDocument construction phase. Also, the table boundary crossing detection (_path_crosses_table_boundary()) only checks if path points are inside/outside the table bbox. It doesn't account for rotated tables. 

### Container Merge Prevention

Merges are blocked when:
- Any element is a container type (table, picture, form, code)
- Any element is a TableStructLabel
- Elements belong to different container ancestors

### Picture Type Special Handling

Pictures with `type` attribute (CHART, INFOGRAPHIC, ILLUSTRATION):
- If **completely untouched** by reading order → treated as atomic visual unit (children exempt)
- If **partially touched** → children not in reading order still get validation errors

---

## Coordinate System Reference

| Context | Origin | Units |
|---------|--------|-------|
| CVAT annotations | TOPLEFT | Pixels |
| PDF parser output | BOTTOMLEFT | Points (72 DPI) |
| OCR output | TOPLEFT | Pixels |
| DoclingDocument provenance | BOTTOMLEFT | Points (scaled) |

### Scale Handling

```
cvat_input_scale: Scale at which CVAT annotations are provided
                  (2.0 for PDFs = 144 DPI, 1.0 for images)

storage_scale: Final scale for DoclingDocument output
               (typically matches cvat_input_scale)
```

### Multi-Page Document Handling

Multi-page documents are processed by concatenating pages horizontally in CVAT coordinate space. Each page gets an offset via the `page_widths` mapping:

```
Page 1: x-offset = 0
Page 2: x-offset = width of page 1
Page 3: x-offset = width of page 1 + width of page 2
...
```

**Comment**: This assumes all pages have uniform height. If pages have different dimensions, the vertical coordinate space becomes ambiguous. Reading order paths must span across pages correctly (level-1 paths should cover all pages), but there's no explicit validation that checks for skipped pages or ensures continuity across page boundaries.

### Text Extraction Strategy

Text is extracted using either PDF native text layer or OCR fallback. The decision is made **per-page** based on:

1. Presence of text layer in PDF
2. Text quality heuristics (if native text exists)

Quality check thresholds:
- Individual cell quality threshold: 0.7
- Maximum low-quality cell ratio: 5%

If text layer is missing OR quality is poor: fallback to OCR.

**Comment**: This can create inconsistent text extraction within a single document (some pages from PDF, others from OCR), with no clear indication what came from where.

---

## Containment Tree Visualization

```
Document Root
├── body (content_layer=BODY)
│   ├── section_header (level=1)
│   ├── text
│   ├── table ─────────────────────────┐
│   │   ├── [table_row] (structural)   │ NOT in reading order
│   │   ├── [table_column]             │ (TableStructLabel)
│   │   └── [table_merged_cell]        │
│   │                                  │
│   │   └── text (inside table) ───────┤ MUST be in level-2+
│   │       └── [key] (GraphCellLabel) │ reading order
│   │       └── [value]                │
│   │                                  │
│   ├── picture (type=CHART) ──────────┤
│   │   └── text (inside chart)        │ Exempt if picture
│   │                                  │ completely untouched
│   ├── list (GroupLabel.LIST) ────────┤
│   │   ├── list_item (level=1)        │
│   │   │   └── text (list content)    │ Group path creates
│   │   ├── list_item (level=1)        │ shared container
│   │   └── [sublist] ─────────────────┤
│   │       └── list_item (level=2)    │ Sublist under parent
│   │                                  │
```

---

## Quick Reference: Validation Severity

| Severity | Meaning | Conversion |
|----------|---------|------------|
| FATAL | Critical structural error | Blocked |
| ERROR | Significant issue | Proceeds with warning |
| WARNING | Minor issue | Proceeds normally |

### FATAL Conditions
- Missing level-1 reading order (unless single-container document)
- Multiple level-1 reading order paths

### ERROR Conditions
- Elements not touched by reading order
- Table children not in level-2+ reading order
- Merge/group with mixed labels or content_layers
- Caption/footnote path with no container
- to_value path not connecting exactly 2 elements
- GraphCellLabel without to_value connection
- TableStructLabel outside table container

### WARNING Conditions
- Backwards merge direction (auto-corrected)
- Missing level/content_layer attributes
- Unrecognized attributes
- Control points not hitting elements

---

## Validation vs Conversion Behavior

Validation is **advisory** (except FATAL severity). Elements with ERROR-level validation issues still proceed to conversion.

**Important distinction**:
- **FATAL errors**: Conversion is blocked entirely
- **ERROR errors**: Validation reports the issue, but conversion proceeds
- **WARNING errors**: Validation notes the issue, conversion proceeds normally

**Comment**: Elements that fail validation aren't necessarily skipped during conversion. This means a document with multiple ERROR-level validation issues can still produce DoclingDocument output. There's no guarantee that a converted document is valid according to DoclingDocument's own validation rules (`validate_tree()`). The conversion may produce documents with broken parent-child relationships, duplicate elements, or missing required fields that only surface when the output is loaded or validated downstream.

---

## Design Issues for Rewrite Consideration

### Fundamental Problems

**1. Intransparent Post-Hoc Fixes**

The pipeline relies on multiple cleanup passes that compensate for earlier stages making incorrect decisions:
- `_resolve_reading_order_conflicts()` - fixes path mapping conflicts
- `promote_table_cross_boundary_reading_order()` - fixes missed container promotions
- `_ensure_tree_parent_consistency()` - fixes broken parent/child pointers from mutations
- `_prune_empty_groups()` - removes groups created before children confirmed
- Bidirectional caption/footnote attempts - fixes direction errors from path mapping

Each fix creates N² complexity where later stages must understand and correct all earlier mistakes. This makes the code unmaintainable and debugging extremely difficult.

**2. Independent Validation and Conversion Logic**

Validator and converter don't share a contract:
- Validation passing doesn't guarantee conversion succeeds
- Conversion succeeding doesn't guarantee valid DoclingDocument output
- Validator checks constraints conversion ignores (e.g., spatial containment)

**3. Implicit Assumptions and Hardcoded Constraints**

Critical assumptions buried in code without explicit verification:
- 5px proximity threshold hardcoded, not adjustable for DPI variations
- 70% IoU threshold for containment may not suit all document types
- Rotation information captured but ignored in all geometric operations

**4. Tree Mutations Without Transaction Safety**

Parent-child relationships mutated without atomic guarantees:
- Reparenting operations split across separate steps (remove from old parent, assign new parent)
- No rollback if later steps fail
- Requires cleanup pass to fix broken invariants created by mutations
- Children lists and parent pointers can become inconsistent mid-operation

### Better Approaches

**Immutable Incremental Construction**
- Build structures incrementally but immutably (return new structure instead of mutating)
- Validate each stage's output before passing to next stage
- Fail fast on violations with clear error messages

**Explicit Contracts Between Stages**
- Define typed inputs/outputs for each stage
- Validation rules = conversion preconditions
- Each stage verifies its inputs and guarantees its outputs
- No shared mutable state between stages

**Two-Pass Processing**
- Pass 1: Analyze and plan (collect all information, detect conflicts)
- Pass 2: Construct (build output based on validated plan)
- Eliminates need for post-hoc fixes and eager allocation

**Transparent Transformations**
- Make all fix-ups explicit and documented in the pipeline
- Each transformation has clear motivation and scope
- Avoid "auto-correct" magic - either error clearly or make correction obvious
- Prefer failing with clear error over producing questionable output

**Transaction-Based Tree Building**
- Build subtrees completely before attaching to parent
- Atomic parent-child updates (both directions in single operation)
- Enforce invariants at API level (DoclingDocument prevents inconsistent mutations)
- Validate tree structure incrementally during construction, not at the end

