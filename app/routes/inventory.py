from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for

from app.services.report_service import ReportService

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


@inventory_bp.route("", methods=["GET"])
def show_inventory():
    report_service: ReportService = current_app.extensions["report_service"]
    storage_summary = report_service.get_storage_inventory_summary()
    return render_template("report.html", storage_summary=storage_summary)


@inventory_bp.route("/download", methods=["GET"])
def download_recent_file():
    storage_path = (request.args.get("path") or "").strip()
    if not _is_safe_storage_path(storage_path):
        flash("Invalid file path.", "error")
        return redirect(url_for("inventory.show_inventory"))

    report_service: ReportService = current_app.extensions["report_service"]
    try:
        payload = report_service.download_storage_file(storage_path)
    except Exception as exc:
        flash(f"Unable to download file: {exc}", "error")
        return redirect(url_for("inventory.show_inventory"))

    filename = PurePosixPath(storage_path).name or "download.csv"
    mimetype = "text/csv" if filename.lower().endswith(".csv") else "application/octet-stream"
    return send_file(
        BytesIO(payload),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


def _is_safe_storage_path(storage_path: str) -> bool:
    # Prevent absolute paths and traversal attempts when local storage backend is active.
    if not storage_path:
        return False
    normalized = storage_path.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return False
    return all(part not in {".", ".."} for part in parts)
