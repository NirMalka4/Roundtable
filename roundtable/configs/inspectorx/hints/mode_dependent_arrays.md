The output's 'mode' selects which detail arrays are mandatory, and one required for
the declared mode is missing or empty.

CORRECTIVE ACTION:
- Look at the top-level 'mode' you emitted.
- For that mode, the corresponding result array must be present AND non-empty:
    - mode "standard"  → 'happy_path_traces' must have ≥1 entry
    - mode "inverted"  → 'breakage_report' must have ≥1 entry
- Either populate the required array with real traces/breakages, or emit the mode that
  matches the work you actually did.
