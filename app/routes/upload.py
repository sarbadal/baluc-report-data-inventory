from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, render_template, request

from app.services.upload_processors import list_upload_processors
from app.services.upload_jobs import UploadJobManager
from app.services.upload_service import UploadService

upload_bp = Blueprint("upload", __name__, url_prefix="/upload")


@upload_bp.route("", methods=["GET", "POST"])
def upload_csv():
    upload_service: UploadService = current_app.extensions["upload_service"]
    categories = list(current_app.extensions["naming_service"].categories())
    configured_processors = current_app.config.get("UPLOAD_PROCESSOR_CLASSES", ["split_daily"])
    processor_options = list_upload_processors()

    if request.method == "POST":
        files = [file for file in request.files.getlist("csv_files") if (file.filename or "").strip()]
        selected_categories = request.form.getlist("categories")

        if not files:
            flash("Please select at least one CSV file.", "error")
            return render_template(
                "upload.html",
                categories=categories,
                upload_results=[],
                row_count=max(1, len(selected_categories)),
                selected_categories=selected_categories,
                configured_processors=configured_processors,
                processor_options=processor_options,
            )

        results = upload_service.handle_bulk_upload(files=files, chosen_categories=selected_categories)
        success_count = sum(1 for result in results if result.success)
        failure_count = len(results) - success_count

        if success_count:
            flash(f"Uploaded {success_count} file(s) successfully.", "success")
        if failure_count:
            flash(f"{failure_count} file(s) failed upload. Check results below.", "error")

        for result in results:
            if result.success:
                flash(result.message, "success")

        return render_template(
            "upload.html",
            categories=categories,
            upload_results=results,
            row_count=max(1, len(files)),
            selected_categories=selected_categories,
            configured_processors=configured_processors,
            processor_options=processor_options,
        )

    return render_template(
        "upload.html",
        categories=categories,
        upload_results=[],
        row_count=1,
        selected_categories=[""],
        configured_processors=configured_processors,
        processor_options=processor_options,
    )


@upload_bp.route("/jobs", methods=["POST"])
def start_upload_job():
    upload_service: UploadService = current_app.extensions["upload_service"]
    upload_job_manager: UploadJobManager = current_app.extensions["upload_job_manager"]

    files = [file for file in request.files.getlist("csv_files") if (file.filename or "").strip()]
    selected_categories = request.form.getlist("categories")
    if not files:
        return jsonify({"error": "Please select at least one CSV file."}), 400

    job_id = upload_job_manager.start_job(
        files=files,
        categories=selected_categories,
        upload_service=upload_service,
    )
    return jsonify({"job_id": job_id}), 202


@upload_bp.route("/jobs/<job_id>", methods=["GET"])
def get_upload_job(job_id: str):
    upload_job_manager: UploadJobManager = current_app.extensions["upload_job_manager"]
    job = upload_job_manager.get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@upload_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_upload_job(job_id: str):
    upload_job_manager: UploadJobManager = current_app.extensions["upload_job_manager"]
    canceled = upload_job_manager.cancel_job(job_id)
    if not canceled:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"status": "cancel_requested", "job_id": job_id})


@upload_bp.route("/processors", methods=["GET"])
def upload_processor_options():
    return jsonify(
        {
            "configured": current_app.config.get("UPLOAD_PROCESSOR_CLASSES", ["split_daily"]),
            "available": list_upload_processors(),
        }
    )
