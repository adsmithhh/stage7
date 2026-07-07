# Runtime resize and scroll restoration summary

Date: 2026-07-07

## Goal

Restore practical windowed runtime navigation so the pygame display can be resized by mouse like a normal desktop window while still allowing access to content outside the visible viewport.

## Completed work

1. Restored viewport-based presentation in the runtime render path so the app keeps a full logical canvas and shows a cropped visible window.
2. Added a right-side vertical scrollbar with track clicking and thumb dragging.
3. Restored the bottom horizontal scrollbar in the current render flow.
4. Kept wheel-based vertical scrolling working with the restored viewport behavior.
5. Extended `UIState` with vertical drag state fields.
6. Fixed viewport-aware mouse hit handling so clicks still line up after scrolling.
7. Updated runtime help text to mention edge tabs, wheel scroll, and bottom/right scrollbars.

## Files changed

- `runtime\app_runtime.py`
- `rendering\rendering.py`
- `rendering\api.py`
- `rendering\backend_software.py`
- `simulation\npc_types.py`

## Result

The runtime is again usable as a mouse-resizable inspection window:

- resize by mouse like a normal window
- use the wheel or right scrollbar for vertical movement
- use the bottom scrollbar for horizontal movement
- keep edge-tab navigation working inside the cropped viewport
