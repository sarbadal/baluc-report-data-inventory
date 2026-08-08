#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _as_bool(value: str, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a shell command and print it for easier debugging."""
    print("\n$", " ".join(cmd))
    result = subprocess.run(cmd, text=True, capture_output=True)

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)

    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def print_dry_run_cmd(cmd: list[str]) -> None:
    print("\n[DRY RUN]", " ".join(cmd))


def print_summary_table(title: str, rows: list[tuple[str, str]]) -> None:
    if not rows:
        return

    key_width = max(len("Setting"), max(len(key) for key, _ in rows))
    val_width = max(len("Value"), max(len(value) for _, value in rows))

    separator = f"+-{'-' * key_width}-+-{'-' * val_width}-+"
    print(f"\n{title}")
    print(separator)
    print(f"| {'Setting'.ljust(key_width)} | {'Value'.ljust(val_width)} |")
    print(separator)
    for key, value in rows:
        print(f"| {key.ljust(key_width)} | {value.ljust(val_width)} |")
    print(separator)


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            f"Required command '{name}' was not found in PATH. Install Google Cloud SDK first."
        )


def bucket_exists(bucket_name: str, project_id: str, dry_run: bool = False) -> bool:
    cmd = [
        "gcloud",
        "storage",
        "buckets",
        "describe",
        f"gs://{bucket_name}",
        "--project",
        project_id,
    ]

    if dry_run:
        print_dry_run_cmd(cmd)
        return False

    result = run_cmd(
        cmd,
        check=False,
    )
    return result.returncode == 0


def create_bucket_if_missing(bucket_name: str, project_id: str, bucket_location: str, dry_run: bool = False) -> None:
    if bucket_exists(bucket_name, project_id, dry_run=dry_run):
        print(f"Bucket already exists: gs://{bucket_name}")
        return

    cmd = [
        "gcloud",
        "storage",
        "buckets",
        "create",
        f"gs://{bucket_name}",
        "--project",
        project_id,
        "--location",
        bucket_location,
        "--uniform-bucket-level-access",
    ]

    if dry_run:
        print(f"Bucket check skipped in dry-run; this create would be attempted for: gs://{bucket_name}")
        print_dry_run_cmd(cmd)
        return

    print(f"Bucket not found. Creating: gs://{bucket_name}")
    run_cmd(cmd)


def sync_static_files(
    static_dir: Path,
    bucket_name: str,
    static_gcs_prefix: str,
    dry_run: bool = False,
) -> None:
    if not static_dir.exists() or not static_dir.is_dir():
        raise RuntimeError(f"Static directory does not exist: {static_dir}")

    # rsync uploads and updates files in one command.
    normalized_prefix = static_gcs_prefix.strip().strip("/") or "static"
    cmd = [
        "gcloud",
        "storage",
        "rsync",
        "--recursive",
        str(static_dir),
        f"gs://{bucket_name}/{normalized_prefix}",
    ]
    if dry_run:
        print_dry_run_cmd(cmd)
        return
    run_cmd(cmd)


def ensure_public_object_access(bucket_name: str, dry_run: bool = False) -> None:
    """Grant public read access to bucket objects for static hosting."""
    cmd = [
        "gcloud",
        "storage",
        "buckets",
        "add-iam-policy-binding",
        f"gs://{bucket_name}",
        "--member=allUsers",
        "--role=roles/storage.objectViewer",
    ]

    if dry_run:
        print("\n[DRY RUN]", " ".join(cmd))
        return

    run_cmd(cmd)


def load_env_file(env_file: Path) -> dict[str, str]:
    """Load KEY=VALUE pairs from a .env style file."""
    if not env_file.exists() or not env_file.is_file():
        raise RuntimeError(f"Env file does not exist: {env_file}")

    env_vars: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            raise RuntimeError(f"Invalid env line {line_number} in {env_file}: '{raw_line}'")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            raise RuntimeError(f"Invalid env line {line_number} in {env_file}: empty key")

        # Remove matching quotes around values: KEY="value" or KEY='value'
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        env_vars[key] = value

    return env_vars


def resolve_deployment_value(
    cli_value: str | None,
    env_vars: dict[str, str],
    env_keys: list[str],
    default: str = "",
) -> str:
    if cli_value:
        return str(cli_value).strip()

    for env_key in env_keys:
        raw = env_vars.get(env_key, "")
        value = str(raw).strip()
        if value:
            return value

    return default


def build_runtime_env_vars(deployment_env: dict[str, str]) -> dict[str, str]:
    # Push only app runtime keys, not deployment-control keys.
    return {
        key: value
        for key, value in deployment_env.items()
        if not key.startswith("DEPLOY_")
    }


def normalize_env_type(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    if value in {"prod", "production"}:
        return "prod"
    if value in {"dev", "development"}:
        return "dev"
    return ""


@dataclass(frozen=True)
class DeployFunctionRequest:
    function_name: str
    project_id: str
    region: str
    runtime: str
    source_dir: Path
    entry_point: str
    env_vars: dict[str, str]
    allow_unauthenticated: bool


def deploy_function(request: DeployFunctionRequest, dry_run: bool = False) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as temp_env_file:
        json.dump(request.env_vars, temp_env_file)
        temp_env_path = Path(temp_env_file.name)

    cmd = [
        "gcloud",
        "functions",
        "deploy",
        request.function_name,
        "--gen2",
        "--project",
        request.project_id,
        "--region",
        request.region,
        "--runtime",
        request.runtime,
        "--source",
        str(request.source_dir),
        "--entry-point",
        request.entry_point,
        "--trigger-http",
        "--env-vars-file",
        str(temp_env_path),
        "--quiet",
    ]

    if request.allow_unauthenticated:
        cmd.append("--allow-unauthenticated")

    try:
        if dry_run:
            print(
                f"\n[DRY RUN] Runtime env vars keys ({len(request.env_vars)}): "
                f"{', '.join(sorted(request.env_vars.keys()))}"
            )
            print_dry_run_cmd(cmd)
        else:
            run_cmd(cmd)
    finally:
        temp_env_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy this Flask app to Google Cloud Functions (Gen2), ensure a static "
            "asset bucket exists, and upload app/static files."
        )
    )
    parser.add_argument("--project-id", help="Google Cloud project ID")
    parser.add_argument("--region", help="GCP region for deployment")
    parser.add_argument("--function-name", help="Cloud Function name")
    parser.add_argument("--entry-point", help="Python function entry point")
    parser.add_argument("--runtime", help="Cloud Functions runtime")
    parser.add_argument(
        "--bucket-name",
        help="Primary GCS data bucket name for uploaded files (without gs://)",
    )
    parser.add_argument(
        "--static-bucket-name",
        help="GCS bucket name for static assets (without gs://)",
    )
    parser.add_argument(
        "--bucket-location",
        help="Bucket location (for example: US, us-central1, asia-south1)",
    )
    parser.add_argument(
        "--static-dir",
        help="Local static files directory to upload",
    )
    parser.add_argument(
        "--static-gcs-prefix",
        help="GCS path prefix where static files are uploaded (default: static)",
    )
    parser.add_argument(
        "--static-asset-base-url",
        help=(
            "Public base URL for static assets. If omitted, the script builds "
            "https://storage.googleapis.com/<bucket>/<prefix>"
        ),
    )
    parser.add_argument(
        "--static-asset-version",
        help=(
            "Static asset cache-busting token. If omitted, uses DEPLOY_STATIC_ASSET_VERSION/"
            "STATIC_ASSET_VERSION from env file, else auto-generates UTC timestamp."
        ),
    )
    parser.add_argument(
        "--source-dir",
        help="Source directory passed to gcloud functions deploy",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env style file to load and pass as Cloud Function env vars",
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow public HTTP access to the deployed function",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print deployment commands and resolved config without executing gcloud actions",
    )
    parser.add_argument(
        "--dry-run-summary",
        action="store_true",
        help="Print a compact table of resolved deployment settings before command output",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if not args.dry_run:
            require_command("gcloud")

        project_root = Path(__file__).resolve().parent
        env_file = (project_root / args.env_file).resolve()
        deployment_env = load_env_file(env_file)

        project_id = resolve_deployment_value(
            args.project_id,
            deployment_env,
            ["DEPLOY_PROJECT_ID", "GCP_PROJECT_ID", "PROJECT_ID"],
        )
        if not project_id:
            raise RuntimeError(
                "Missing project id. Set --project-id or define DEPLOY_PROJECT_ID (or GCP_PROJECT_ID/PROJECT_ID) in .env"
            )

        data_bucket_name = resolve_deployment_value(
            args.bucket_name,
            deployment_env,
            ["DEPLOY_BUCKET_NAME", "BUCKET_NAME"],
        )
        if not data_bucket_name:
            raise RuntimeError(
                "Missing data bucket name. Set --bucket-name or define DEPLOY_BUCKET_NAME (or BUCKET_NAME) in .env"
            )

        static_bucket_name = resolve_deployment_value(
            args.static_bucket_name,
            deployment_env,
            ["DEPLOY_STATIC_BUCKET_NAME", "STATIC_BUCKET"],
            default=data_bucket_name,
        )

        region = resolve_deployment_value(
            args.region,
            deployment_env,
            ["DEPLOY_REGION", "REGION"],
            default="us-central1",
        )
        function_name = resolve_deployment_value(
            args.function_name,
            deployment_env,
            ["DEPLOY_FUNCTION_NAME", "FUNCTION_NAME"],
            default="baluc-report",
        )
        entry_point = resolve_deployment_value(
            args.entry_point,
            deployment_env,
            ["DEPLOY_ENTRY_POINT", "ENTRY_POINT"],
            default="entry_point",
        )
        runtime = resolve_deployment_value(
            args.runtime,
            deployment_env,
            ["DEPLOY_RUNTIME", "RUNTIME"],
            default="python312",
        )
        bucket_location = resolve_deployment_value(
            args.bucket_location,
            deployment_env,
            ["DEPLOY_BUCKET_LOCATION", "BUCKET_LOCATION"],
            default="US",
        )
        static_dir_value = resolve_deployment_value(
            args.static_dir,
            deployment_env,
            ["DEPLOY_STATIC_DIR", "STATIC_DIR"],
            default="app/static",
        )
        source_dir_value = resolve_deployment_value(
            args.source_dir,
            deployment_env,
            ["DEPLOY_SOURCE_DIR", "SOURCE_DIR"],
            default=".",
        )
        static_gcs_prefix = resolve_deployment_value(
            args.static_gcs_prefix,
            deployment_env,
            ["DEPLOY_STATIC_GCS_PREFIX", "STATIC_GCS_PREFIX"],
            default="static",
        ).strip().strip("/")
        if not static_gcs_prefix:
            static_gcs_prefix = "static"

        static_asset_base_url = resolve_deployment_value(
            args.static_asset_base_url,
            deployment_env,
            ["DEPLOY_STATIC_ASSET_BASE_URL", "STATIC_ASSET_BASE_URL"],
            default="",
        ).strip().rstrip("/")
        if not static_asset_base_url:
            static_asset_base_url = (
                f"https://storage.googleapis.com/{static_bucket_name}/{static_gcs_prefix}"
            )

        static_asset_version = resolve_deployment_value(
            args.static_asset_version,
            deployment_env,
            ["DEPLOY_STATIC_ASSET_VERSION", "STATIC_ASSET_VERSION"],
            default="",
        ).strip()
        if not static_asset_version:
            static_asset_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        allow_unauthenticated = bool(args.allow_unauthenticated) or _as_bool(
            deployment_env.get("DEPLOY_ALLOW_UNAUTHENTICATED", ""),
            default=False,
        )

        static_dir = (project_root / static_dir_value).resolve()
        source_dir = (project_root / source_dir_value).resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            raise RuntimeError(f"Source directory does not exist: {source_dir}")

        if not (source_dir / "main.py").exists():
            raise RuntimeError(
                "Source directory must contain main.py for entry_point-based deployment: "
                f"{source_dir}"
            )

        env_vars = build_runtime_env_vars(deployment_env)

        effective_env_type = normalize_env_type(env_vars.get("ENV_TYPE", ""))
        if not effective_env_type:
            effective_env_type = normalize_env_type(env_vars.get("APP_ENV", ""))
        if not effective_env_type:
            effective_env_type = "prod"

        env_vars["ENV_TYPE"] = effective_env_type
        if effective_env_type == "prod":
            env_vars.setdefault("FLASK_ENV", "production")
            env_vars.setdefault("APP_ENV", "prod")
            env_vars.setdefault("STORAGE_BACKEND", "gcs")
        else:
            env_vars.setdefault("FLASK_ENV", "development")
            env_vars.setdefault("APP_ENV", "dev")
            env_vars.setdefault("STORAGE_BACKEND", "local")

        env_vars["GCS_BUCKET_NAME"] = data_bucket_name
        env_vars["STATIC_BUCKET"] = static_bucket_name
        env_vars["STATIC_GCS_PREFIX"] = static_gcs_prefix
        env_vars["STATIC_ASSET_BASE_URL"] = static_asset_base_url
        env_vars["STATIC_ASSET_VERSION"] = static_asset_version

        if args.dry_run_summary:
            summary_rows: list[tuple[str, str]] = [
                ("project_id", project_id),
                ("region", region),
                ("function_name", function_name),
                ("entry_point", entry_point),
                ("runtime", runtime),
                ("data_bucket_name", data_bucket_name),
                ("static_bucket_name", static_bucket_name),
                ("static_gcs_prefix", static_gcs_prefix),
                ("static_asset_base_url", static_asset_base_url),
                ("static_asset_version", static_asset_version),
                ("bucket_location", bucket_location),
                ("effective_env_type", effective_env_type),
                ("static_dir", str(static_dir)),
                ("source_dir", str(source_dir)),
                ("allow_unauthenticated", "true" if allow_unauthenticated else "false"),
                ("env_file", str(env_file)),
                ("runtime_env_var_count", str(len(env_vars))),
                ("dry_run", "true" if args.dry_run else "false"),
            ]
            print_summary_table("Resolved deployment settings", summary_rows)

        print("Starting deployment workflow...")
        print(f"Project: {project_id}")
        print(f"Function: {function_name}")
        print(f"Region: {region}")
        print(f"Data bucket: gs://{data_bucket_name}")
        print(f"Static bucket: gs://{static_bucket_name}")
        print(f"Static prefix: {static_gcs_prefix}")
        print(f"Static base URL: {static_asset_base_url}")
        print(f"Static asset version: {static_asset_version}")
        print(f"Env file: {env_file}")
        print(f"Dry run: {'yes' if args.dry_run else 'no'}")

        create_bucket_if_missing(data_bucket_name, project_id, bucket_location, dry_run=args.dry_run)
        create_bucket_if_missing(static_bucket_name, project_id, bucket_location, dry_run=args.dry_run)
        ensure_public_object_access(static_bucket_name, dry_run=args.dry_run)
        sync_static_files(
            static_dir,
            static_bucket_name,
            static_gcs_prefix,
            dry_run=args.dry_run,
        )
        deploy_request = DeployFunctionRequest(
            function_name=function_name,
            project_id=project_id,
            region=region,
            runtime=runtime,
            source_dir=source_dir,
            entry_point=entry_point,
            env_vars=env_vars,
            allow_unauthenticated=allow_unauthenticated,
        )
        deploy_function(
            request=deploy_request,
            dry_run=args.dry_run,
        )

        print("\nDeployment completed in production mode." if not args.dry_run else "\nDry run completed.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nDeployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# python deployment.py --env-file deploy.env
    
