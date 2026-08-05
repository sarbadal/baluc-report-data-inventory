from flask import Flask
from flask import flash, redirect, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from app.config import Config, validate_processing_category_configs
from app.routes.home import home_bp
from app.routes.inventory import inventory_bp
from app.routes.upload import upload_bp
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
    storage_provider = build_storage_provider(app.config)
    upload_processors = build_upload_processors(
        processor_names=app.config.get("UPLOAD_PROCESSOR_CLASSES", ["split_daily"]),
        category_configs=app.config.get("PROCESSING_CATEGORY_CONFIGS", {}),
    )
    upload_service = UploadService(
        naming_service=naming_service,
        storage_provider=storage_provider,
        upload_base_prefix=app.config.get("UPLOAD_BASE_PREFIX", ""),
        upload_processors=upload_processors,
    )

    report_service = ReportService(storage_provider=storage_provider)

    app.extensions["naming_service"] = naming_service
    app.extensions["storage_provider"] = storage_provider
    app.extensions["upload_service"] = upload_service
    app.extensions["upload_job_manager"] = UploadJobManager()
    app.extensions["report_service"] = report_service

    app.register_blueprint(home_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(inventory_bp)

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
