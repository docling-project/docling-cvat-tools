"""List hierarchy processing via level propagation.

This module implements the level propagation model for building list hierarchies
without the CVAT lookahead heuristics. Lists are built using explicit level
attributes and optional group paths that define boundaries.

Annotation Contract:
- Every element in reading order carries a `level` attribute (default: None/unset)
- Only list_item and section_header typically have explicit levels
- Level propagation: unset elements inherit the last explicitly set level

Processing Rules:
1. Level propagation: maintain current_level state
2. Group paths define list boundaries explicitly
3. Without group paths: lists terminate on lower/unset levels
4. Nesting: higher levels create sublists, lower levels are siblings
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from docling_core.types.doc.labels import DocItemLabel

from .models import ListState, LSElement, LSPath

logger = logging.getLogger(__name__)


def build_group_membership(
    groups: List[List[LSElement]],
    group_paths: List[LSPath],
) -> Dict[str, Set[str]]:
    """Build a mapping from group path IDs to their member element IDs.

    Args:
        groups: Resolved group path element lists
        group_paths: Original group path objects (for ls_id access)

    Returns:
        Dict mapping group ls_id to set of element ls_ids
    """
    membership: Dict[str, Set[str]] = {}

    for i, group_elements in enumerate(groups):
        if i < len(group_paths):
            group_id = group_paths[i].ls_id
            membership[group_id] = {el.ls_id for el in group_elements}

    return membership


def build_element_to_groups(
    group_membership: Dict[str, Set[str]],
) -> Dict[str, List[str]]:
    """Build a reverse mapping from element IDs to their group IDs.

    Args:
        group_membership: Dict mapping group ls_id to set of element ls_ids

    Returns:
        Dict mapping element ls_id to list of group ls_ids it belongs to
    """
    element_to_groups: Dict[str, List[str]] = {}

    for group_id, element_ids in group_membership.items():
        for element_id in element_ids:
            if element_id not in element_to_groups:
                element_to_groups[element_id] = []
            element_to_groups[element_id].append(group_id)

    return element_to_groups


def get_effective_level(
    element: LSElement,
    state: ListState,
) -> Optional[int]:
    """Get the effective level for an element using level propagation.

    If the element has an explicit level, update current_level and return it.
    Otherwise, inherit the current_level (last explicitly set level).

    Args:
        element: The element to get level for
        state: Current list processing state (will be mutated)

    Returns:
        Effective level, or None if no level has been set yet
    """
    if element.level is not None:
        # Explicit level - update state and return
        state.current_level = element.level
        return element.level
    else:
        # Inherit propagated level
        return state.current_level


def is_list_item_element(element: LSElement) -> bool:
    """Check if an element is a list item."""
    return element.label == DocItemLabel.LIST_ITEM


def should_terminate_list(
    element: LSElement,
    state: ListState,
    element_to_groups: Dict[str, List[str]],
) -> bool:
    """Determine if the current list should be terminated before this element.

    With group paths: element not in active group terminates the list.
    Without group paths: lower explicit level or unset level terminates.

    Args:
        element: The next element in reading order
        state: Current list processing state
        element_to_groups: Mapping of element IDs to their groups

    Returns:
        True if the current list should be terminated
    """
    if not state.level_stack:
        # No active list
        return False

    # Check group-based termination
    if state.active_group_id is not None:
        element_groups = element_to_groups.get(element.ls_id, [])
        if state.active_group_id not in element_groups:
            # Element is not in the active group - terminate
            return True
        return False

    # Fallback: level-based termination (when no group paths)
    if not is_list_item_element(element):
        # Non-list-item elements don't terminate lists by themselves
        # unless they have an explicit lower level
        if element.level is not None and element.level < state.level_stack[-1]:
            return True
        return False

    # For list items, check level conditions
    if element.level is None:
        # Unset level terminates the list
        return True

    if element.level < state.level_stack[-1]:
        # Lower level terminates inner lists
        return True

    return False


def should_start_list(
    element: LSElement,
    state: ListState,
    element_to_groups: Dict[str, List[str]],
) -> bool:
    """Determine if a new list should be started for this element.

    Args:
        element: The element being processed
        state: Current list processing state
        element_to_groups: Mapping of element IDs to their groups

    Returns:
        True if a new list should be started
    """
    if not is_list_item_element(element):
        return False

    # Check if element is part of a group
    element_groups = element_to_groups.get(element.ls_id, [])
    if element_groups and state.active_group_id is None:
        # Element is in a group but no active group - start new list
        return True

    if not state.level_stack:
        # No active list and this is a list item - start list
        return True

    # Check for nested list (higher level)
    effective_level = (
        element.level if element.level is not None else state.current_level
    )
    if effective_level is not None and state.level_stack:
        if effective_level > state.level_stack[-1]:
            # Higher level - start nested list
            return True

    return False


def get_nesting_depth_change(
    element: LSElement,
    state: ListState,
) -> int:
    """Calculate how many list levels to close or open for this element.

    Positive = open new nested lists
    Negative = close inner lists
    Zero = same level

    Args:
        element: The element being processed
        state: Current list processing state

    Returns:
        Depth change value
    """
    if not state.level_stack:
        return 0

    effective_level = (
        element.level if element.level is not None else state.current_level
    )
    if effective_level is None:
        return 0

    current_depth_level = state.level_stack[-1]

    if effective_level > current_depth_level:
        # Deeper nesting
        return 1  # Open one level at a time
    elif effective_level < current_depth_level:
        # Count how many levels to close
        levels_to_close = 0
        for level in reversed(state.level_stack):
            if level > effective_level:
                levels_to_close += 1
            else:
                break
        return -levels_to_close
    else:
        return 0


def process_list_element(
    element: LSElement,
    state: ListState,
    element_to_groups: Dict[str, List[str]],
) -> Tuple[bool, bool, int]:
    """Process an element for list hierarchy tracking.

    Args:
        element: The element to process
        state: Current list state (will be mutated)
        element_to_groups: Mapping of element IDs to their groups

    Returns:
        Tuple of:
        - should_terminate: Whether to close the current list first
        - should_start: Whether to start a new list
        - depth_change: How many levels to adjust (-N to close, +N to open)
    """
    should_terminate = should_terminate_list(element, state, element_to_groups)
    should_start = should_start_list(element, state, element_to_groups)
    depth_change = get_nesting_depth_change(element, state)

    # Update state for level propagation
    get_effective_level(element, state)

    # Update active group if element belongs to a group
    element_groups = element_to_groups.get(element.ls_id, [])
    if element_groups and is_list_item_element(element):
        # Use the first group this element belongs to as active
        state.active_group_id = element_groups[0]

    return should_terminate, should_start, depth_change


def update_list_state_on_start(
    state: ListState,
    level: int,
    group_id: Optional[str] = None,
) -> None:
    """Update state when starting a new list.

    Args:
        state: List state to update
        level: The level of the list being started
        group_id: Optional group ID if list is defined by a group path
    """
    state.level_stack.append(level)
    if group_id is not None:
        state.active_group_id = group_id


def update_list_state_on_close(
    state: ListState,
    levels_to_close: int = 1,
) -> None:
    """Update state when closing list level(s).

    Args:
        state: List state to update
        levels_to_close: Number of nesting levels to close
    """
    for _ in range(min(levels_to_close, len(state.level_stack))):
        state.level_stack.pop()

    if not state.level_stack:
        state.active_group_id = None


def reset_list_state(state: ListState) -> None:
    """Reset list state when a list is fully terminated.

    Args:
        state: List state to reset
    """
    state.level_stack.clear()
    state.active_group_id = None
    # Note: current_level is NOT reset - it propagates across lists
