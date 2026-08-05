from flask import Blueprint, current_app, render_template

home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def index():
    storage_backend = str(current_app.config.get("STORAGE_BACKEND", "local")).strip().lower()
    gcs_bucket = str(current_app.config.get("GCS_BUCKET_NAME", "")).strip()
    configured_processors = list(current_app.config.get("UPLOAD_PROCESSOR_CLASSES", ["split_daily"]))

    return render_template(
        "home.html",
        storage_backend=storage_backend,
        gcs_bucket=gcs_bucket,
        configured_processors=configured_processors,
    )
