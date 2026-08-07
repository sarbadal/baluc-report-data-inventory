from flask import Flask
from flask import flash, redirect, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import Config, validate_processing_category_configs
from app.routes.home import home_bp
from app.routes.inventory import inventory_bp
from app.routes.upload import upload_bp
from app.services.category_detection import CategoryDetectionService
from app.services.naming import FilenameConventionService
from app.services.upload_jobs import UploadJobManager
from app.services.upload_processors import build_upload_processors
from app.services.report_service import ReportService
from app.services.storage import build_storage_provider
from app.services.upload_service import UploadService


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    validate_processing_category_configs(
        upload_type_rules=app.config.get("UPLOAD_TYPE_RULES", {}),
        processing_category_configs=app.config.get("PROCESSING_CATEGORY_CONFIGS", {}),
    )

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = "dev-secret-key"

    naming_service = FilenameConventionService(app.config["UPLOAD_TYPE_RULES"])
    category_detection_service = CategoryDetectionService(
        naming_service=naming_service,
        processing_category_configs=app.config.get("PROCESSING_CATEGORY_CONFIGS", {}),
    )
    storage_provider = build_storage_provider(app.config)
    upload_processors = build_upload_processors(
        processor_names=app.config.get("UPLOAD_PROCESSOR_CLASSES", ["split_daily"]),
        category_configs=app.config.get("PROCESSING_CATEGORY_CONFIGS", {}),
    )
    upload_service = UploadService(
        naming_service=naming_service,
        category_detection_service=category_detection_service,
        storage_provider=storage_provider,
        processing_category_configs=app.config.get("PROCESSING_CATEGORY_CONFIGS", {}),
        category_filename_rule_configs={
            "mapping": app.config.get("MAPPING_STORAGE_RULE_CONFIGS", []),
        },
        upload_base_prefix=app.config.get("UPLOAD_BASE_PREFIX", ""),
        upload_processors=upload_processors,
    )

    report_service = ReportService(storage_provider=storage_provider)

    app.extensions["naming_service"] = naming_service
    app.extensions["category_detection_service"] = category_detection_service
    app.extensions["storage_provider"] = storage_provider
    app.extensions["upload_service"] = upload_service
    app.extensions["upload_job_manager"] = UploadJobManager()
    app.extensions["report_service"] = report_service

    app.register_blueprint(home_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(inventory_bp)

    static_base_url = _resolve_static_asset_base_url(app.config)
    if static_base_url:
        app.config["STATIC_ASSET_BASE_URL"] = static_base_url

    @app.context_processor
    def inject_static_url_for():
        def static_url_for(filename: str) -> str:
            base = str(app.config.get("STATIC_ASSET_BASE_URL", "")).strip().rstrip("/")
            if base:
                return f"{base}/{filename.lstrip('/')}"
            return url_for("static", filename=filename)

        return {"static_url_for": static_url_for}

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(error: RequestEntityTooLarge):
        del error
        max_bytes = int(app.config.get("MAX_CONTENT_LENGTH", 0))
        max_mb = max_bytes // (1024 * 1024) if max_bytes else 0
        flash(
            f"Upload is too large. Current limit is {max_mb} MB. "
            "Increase MAX_UPLOAD_SIZE_MB (or MAX_UPLOAD_SIZE_BYTES) and restart the app.",
            "error",
        )
        if request.path.startswith("/upload"):
            return redirect(url_for("upload.upload_csv"))
        return redirect(url_for("home.index"))

    return app


def _resolve_static_asset_base_url(config: Config) -> str:
    explicit_base_url = str(config.get("STATIC_ASSET_BASE_URL", "")).strip().rstrip("/")
    if explicit_base_url:
        return explicit_base_url

    app_env = str(config.get("APP_ENV", "development")).strip().lower()
    if app_env != "production":
        return ""

    bucket_name = str(config.get("GCS_BUCKET_NAME", "")).strip()
    if not bucket_name:
        return ""

    static_prefix = str(config.get("STATIC_GCS_PREFIX", "static")).strip("/")
    if static_prefix:
        return f"https://storage.googleapis.com/{bucket_name}/{static_prefix}"
    return f"https://storage.googleapis.com/{bucket_name}"
