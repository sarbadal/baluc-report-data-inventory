# BaluC CSV Upload and Inventory Service

BaluC is a Flask-based CSV ingestion service with category-aware processing, configurable file naming, and storage inventory reporting.

Core capabilities:

- Upload one or more CSV files from UI.
- Auto-detect or user-select category.
- Apply configurable processor pipeline.
- Support file types:
	- `fact`: runs configured processors (for example split by date).
	- `dim`: bypasses split and uploads as a single file.
- Store to local filesystem or Google Cloud Storage.
- View inventory dashboard with per-category min/max file dates and missing-date gaps.

## Project Layout

- `main.py`: Flask app entrypoint.
- `app/app_factory.py`: app wiring and service registration.
- `app/config.py`: environment and JSON config loading.
- `app/resources/upload_types.json`: category rules and filename patterns.
- `app/resources/processing/*.json`: per-category processing configs.
- `app/resources/processing/mapping_types/*.json`: per-mapping-type filename rules.
- `app/services/upload_service.py`: upload orchestration and storage path generation.
- `app/services/upload_processors.py`: processor implementations/registry.
- `app/services/category_detection.py`: CSV and filename scoring logic.
- `app/services/report_service.py`: inventory aggregation and date-gap logic.
- `app/templates/upload.html`: upload page.
- `app/templates/report.html`: inventory page.

## Quick Start

1. Create and activate virtual environment.
2. Install dependencies.
3. Create `.env` from `.env.example`.
4. Run the app.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open:

- Upload: `http://127.0.0.1:5000/upload`
- Inventory: `http://127.0.0.1:5000/inventory`

## Configuration

Environment variables are loaded from `.env` by `app/config.py`.

Important variables:

- `STORAGE_BACKEND`: `local` or `gcs`
- `LOCAL_STORAGE_ROOT`: local storage folder (default `storage`)
- `GCS_BUCKET_NAME`: required when `STORAGE_BACKEND=gcs`
- `UPLOAD_BASE_PREFIX`: optional prefix in storage paths
- `UPLOAD_PROCESSOR_CLASSES`: comma-separated processor chain
- `UPLOAD_TYPE_RULES_FILE`: optional path to category rule JSON
- `UPLOAD_ENABLED_CATEGORIES`: optional category whitelist
- `PROCESSING_CONFIG_DIR`: folder with category processing JSON files
- `MAPPING_STORAGE_RULES_DIR`: folder with mapping filename-rule JSON files
- `MAX_UPLOAD_SIZE_MB` / `MAX_UPLOAD_SIZE_BYTES`: upload size limit

## Category and File Type Rules

Category rules are defined in `app/resources/upload_types.json`.

Current categories:

- `contact`
- `ev`
- `print`
- `mapping`

Each category can define:

- `match_patterns`: filename regex patterns
- `output_stem`: default output filename stem
- `default_file_type`: `fact` or `dim`

### Processing Config per Category

Each enabled category must have a matching JSON file in `app/resources/processing`.

Typical keys:

- `field_mapping`: mapping object
- `split_date_column`: required for categories that default to `fact`
- `content_hints`: optional scoring hints for detection
- `storage_options`: optional storage path/naming overrides

For `mapping` category (default `dim`), `split_date_column` is not required.

## Upload Processor Pipeline

Registered processors in `app/services/upload_processors.py`:

- `identity`
- `rename_fields`
- `keep_config_columns`
- `split_daily`

`UPLOAD_PROCESSOR_CLASSES` controls execution order.

Examples:

```env
UPLOAD_PROCESSOR_CLASSES=split_daily
```

```env
UPLOAD_PROCESSOR_CLASSES=rename_fields,keep_config_columns,split_daily
```

Behavior notes:

- For `fact` files: pipeline runs in order.
- For `dim` files: processors are bypassed and upload happens as a single output file.

## Mapping Category: Separate JSON Per Mapping Type

`mapping` category supports dedicated filename rules per mapping type using JSON files in:

- `app/resources/processing/mapping_types`

Each file can contain:

- `mapping_type`
- `match_patterns`
- `output_filename`

These are loaded via `MAPPING_STORAGE_RULES_DIR` and used by upload path logic.

`mapping` storage behavior is controlled by `app/resources/processing/mapping.json`:

- `path_mode=category_only`: stores under `mapping/<filename>.csv` (no year/month folders)
- `use_original_filename=true`: fallback when no rule matches
- `default_output_filename`: final fallback

## Inventory Page

Inventory shows:

- total files, categories, total size, last upload
- By Category table:
	- file count
	- total size
	- min file date (parsed from file path/name)
	- max file date (parsed from file path/name)
	- missing dates between month-start(min) and max date
- Recent uploads table with downloadable files

Missing dates are displayed as:

- `-` when none are missing
- expandable details with one date per row when missing dates exist

## Category Detection

Detection combines filename rules and CSV scoring. See:

- `docs/category-detection-logic.md`

## API Endpoints

- `GET /upload`: upload UI
- `POST /upload`: sync multi-file upload
- `POST /upload/jobs`: start async upload job
- `GET /upload/jobs/<job_id>`: poll async status
- `POST /upload/jobs/<job_id>/cancel`: cancel job
- `GET /upload/processors`: list configured and available processors
- `GET /inventory`: storage inventory dashboard
- `GET /inventory/download?path=<storage_path>`: download file

## Notes

- `python main.py` runs Flask app in debug mode.
- In production, configure `APP_ENV=production` and static asset settings as needed.
- If using GCS behind strict proxy/network, ensure auth/network access is available.
