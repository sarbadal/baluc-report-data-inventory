# Category Detection Logic

This document explains how file category is determined during upload, and when an upload is blocked.

## Main Components

- Detection service: [app/services/category_detection.py](app/services/category_detection.py)
- Upload validation flow: [app/services/upload_service.py](app/services/upload_service.py)
- Filename patterns config: [app/resources/upload_types.json](app/resources/upload_types.json)
- Category processing configs (header/content hints):
  - [app/resources/processing/contact.json](app/resources/processing/contact.json)
  - [app/resources/processing/ev.json](app/resources/processing/ev.json)

## Detection Order

1. Filename-based detection
- The detector first checks filename patterns from [app/resources/upload_types.json](app/resources/upload_types.json).
- Matching is case-insensitive regex.
- If matched, category is returned with source as filename and confidence 1.0.

2. CSV-based detection (when filename is not enough)
- If filename does not match, detector scores each category using CSV schema/content:
  - Header score from field mapping overlap.
  - Optional content score from content_hints.column_value_patterns.
  - Split date column bonus when present.
- Best category is selected only if confidence threshold and tie-break conditions are satisfied.

## How CSV Scoring Works

For each category config:

1. Header score
- Compare incoming CSV columns with:
  - Source columns (values from field_mapping)
  - Target columns (keys from field_mapping)
- Use best overlap ratio as header score.

2. Content score (optional)
- Read content_hints.column_value_patterns list.
- For each rule:
  - column: column name to inspect
  - pattern: regex pattern to test values
  - weight: rule importance in weighted average
- All valid rules are counted in total weight.
- Missing or empty columns are treated as zero match (not skipped).

3. Combined score
- If content score exists:
  - final score = header_score * 0.75 + content_score * 0.25
- If split_date_column exists in CSV columns:
  - add 0.08 bonus
- Cap score at 1.0.

## Selected Category Validation (Strict)

When user explicitly selects category during upload:

1. Category name must be valid.
2. If filename clearly maps to another category, upload is rejected immediately.
3. Detector computes scores for all categories on CSV data.
4. Selected category must meet minimum confidence.
5. If another category has a higher score than selected category, upload is rejected.

This prevents incorrect uploads such as selecting EV for a Contact filename/content.

## Important Thresholds

Defined in [app/services/category_detection.py](app/services/category_detection.py):

- min_confidence = 0.45
- tie_break_delta = 0.05

You can tune these values for stricter or looser detection behavior.

## Regex Tips for Config

- Digits only (required): ^\\d+$
- Digits or empty: ^\\d*$
- Fiscal year like 2026-27: ^\\d{4}-\\d{2}$
- Date like 2026-07-31 or 2026/07/31: ^\\d{4}[-/]\\d{2}[-/]\\d{2}$

## Troubleshooting

If correct files are rejected:

1. Verify field_mapping column names match your CSV headers.
2. Check content_hints patterns for over-strict regex.
3. Reduce noisy rules by lowering their weight.
4. Increase strong identifying rules by raising their weight.
5. Re-check min_confidence and tie_break_delta.
