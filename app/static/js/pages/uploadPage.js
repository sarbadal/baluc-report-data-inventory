import { ACTIVE_JOB_KEY, TERMINAL_JOB_STATUSES } from "../modules/constants.js";
import { cancelUploadJob, fetchUploadJob, startUploadJob } from "../modules/uploadJobsApi.js";

(function initUploadPage() {
    const addRowButton = document.getElementById("add-row");
    const rowsContainer = document.getElementById("upload-rows");
    const template = document.getElementById("upload-row-template");
    const uploadForm = document.getElementById("bulk-upload-form");
    const submitButton = document.getElementById("upload-submit");
    const cancelButton = document.getElementById("cancel-upload");
    const liveProgress = document.getElementById("live-progress");
    const liveStatus = document.getElementById("live-status");
    const resultsBlock = document.getElementById("upload-results-block");
    const tableBody = document.querySelector(".results-table tbody");
    const categoryDefaultRaw = uploadForm.dataset.categoryDefaultFileTypes || "{}";
    let categoryDefaultFileTypes = {};
    let pollHandle = null;

    try {
        categoryDefaultFileTypes = JSON.parse(categoryDefaultRaw);
    } catch (error) {
        console.warn("Unable to parse category default file types", error);
        categoryDefaultFileTypes = {};
    }

    if (
        !addRowButton ||
        !rowsContainer ||
        !template ||
        !uploadForm ||
        !submitButton ||
        !cancelButton ||
        !liveProgress ||
        !liveStatus ||
        !resultsBlock ||
        !tableBody
    ) {
        return;
    }

    const applyCategoryDefaultType = function (row, force) {
        const categorySelect = row.querySelector('select[name="categories"]');
        const fileTypeSelect = row.querySelector('select[name="file_types"]');
        if (!categorySelect || !fileTypeSelect) {
            return;
        }

        const category = (categorySelect.value || "").trim();
        const defaultType = categoryDefaultFileTypes[category] || "fact";
        const hasManualOverride = fileTypeSelect.dataset.userOverride === "true";

        if (force || !hasManualOverride) {
            fileTypeSelect.value = defaultType;
            fileTypeSelect.dataset.autoValue = defaultType;
            fileTypeSelect.dataset.userOverride = "false";
        }
    };

    const attachRowBehavior = function (row) {
        const categorySelect = row.querySelector('select[name="categories"]');
        const fileTypeSelect = row.querySelector('select[name="file_types"]');
        if (!categorySelect || !fileTypeSelect) {
            return;
        }

        fileTypeSelect.dataset.autoValue = fileTypeSelect.value || "fact";
        fileTypeSelect.dataset.userOverride = "false";

        categorySelect.addEventListener("change", function () {
            applyCategoryDefaultType(row, false);
        });

        fileTypeSelect.addEventListener("change", function () {
            const autoValue = fileTypeSelect.dataset.autoValue || "fact";
            fileTypeSelect.dataset.userOverride = fileTypeSelect.value !== autoValue ? "true" : "false";
        });

        applyCategoryDefaultType(row, false);
    };

    rowsContainer.querySelectorAll(".upload-row").forEach(function (row) {
        attachRowBehavior(row);
    });

    addRowButton.addEventListener("click", function () {
        const clone = template.content.cloneNode(true);
        rowsContainer.appendChild(clone);
        const newRow = rowsContainer.lastElementChild;
        if (newRow) {
            attachRowBehavior(newRow);
        }
    });

    const renderRow = function (result) {
        const row = document.createElement("tr");

        const fileCell = document.createElement("td");
        fileCell.textContent = result.original_filename || "-";

        const categoryCell = document.createElement("td");
        categoryCell.textContent = result.category || "-";

        const fileTypeCell = document.createElement("td");
        fileTypeCell.textContent = result.file_type || "fact";

        const statusCell = document.createElement("td");
        if (result.is_active) {
            statusCell.textContent = result.status_label || "In Progress";
        } else {
            statusCell.textContent = result.success ? "Success" : "Failed";
        }

        const progressCell = document.createElement("td");
        const progressWrap = document.createElement("div");
        progressWrap.className = "progress-wrap";

        const progressBar = document.createElement("div");
        progressBar.className = "progress-bar";

        const progressFill = document.createElement("div");
        progressFill.className = "progress-fill";

        const progressText = document.createElement("div");
        progressText.className = "progress-text";

        let percent = 0;
        if (result.is_active && typeof result.progress === "string" && result.progress.includes("/")) {
            const parts = result.progress.split("/");
            const done = Number(parts[0]);
            const total = Number(parts[1]);
            if (Number.isFinite(done) && Number.isFinite(total) && total > 0) {
                percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
            }
        } else if (result.is_active && typeof result.progress === "string" && result.progress.includes("%")) {
            const normalized = Number(result.progress.replace("%", "").trim());
            if (Number.isFinite(normalized)) {
                percent = Math.max(0, Math.min(100, Math.round(normalized)));
            }
        } else if (result.success) {
            percent = 100;
        }

        progressFill.style.width = `${percent}%`;
        progressText.textContent = `${percent}%`;

        progressBar.appendChild(progressFill);
        progressWrap.appendChild(progressBar);
        progressWrap.appendChild(progressText);
        progressCell.appendChild(progressWrap);

        const pathCell = document.createElement("td");
        if (Array.isArray(result.uploaded_paths) && result.uploaded_paths.length > 0) {
            const count = document.createElement("div");
            count.className = "path-count";
            count.textContent = `${result.uploaded_paths.length} file(s)`;
            pathCell.appendChild(count);

            const list = document.createElement("ul");
            list.className = "path-list";
            result.uploaded_paths.forEach(function (path) {
                const item = document.createElement("li");
                item.textContent = path;
                list.appendChild(item);
            });
            pathCell.appendChild(list);
        } else {
            pathCell.textContent = result.destination_path || result.message || "-";
        }

        row.appendChild(fileCell);
        row.appendChild(categoryCell);
        row.appendChild(fileTypeCell);
        row.appendChild(statusCell);
        row.appendChild(progressCell);
        row.appendChild(pathCell);
        tableBody.appendChild(row);
    };

    const renderJobState = function (job) {
        liveStatus.textContent = `${job.status.toUpperCase()}: ${job.processed_files}/${job.total_files} files | ${job.current_step}`;
        tableBody.innerHTML = "";
        (job.active_results || []).forEach(function (result) {
            renderRow(result);
        });
        (job.results || []).forEach(function (result) {
            renderRow(result);
        });
    };

    const startPolling = function (jobId) {
        if (!jobId) {
            return;
        }
        if (pollHandle) {
            clearInterval(pollHandle);
        }

        pollHandle = setInterval(async function () {
            const job = await fetchUploadJob(jobId);
            if (!job) {
                liveStatus.textContent = "Upload progress is unavailable.";
                clearInterval(pollHandle);
                pollHandle = null;
                submitButton.disabled = false;
                cancelButton.style.display = "none";
                localStorage.removeItem(ACTIVE_JOB_KEY);
                return;
            }

            renderJobState(job);

            if (TERMINAL_JOB_STATUSES.has(job.status)) {
                clearInterval(pollHandle);
                pollHandle = null;
                submitButton.disabled = false;
                cancelButton.style.display = "none";
                localStorage.removeItem(ACTIVE_JOB_KEY);
            }
        }, 1200);
    };

    uploadForm.addEventListener("submit", async function (event) {
        event.preventDefault();

        const formData = new FormData(uploadForm);
        submitButton.disabled = true;
        cancelButton.style.display = "inline-block";
        liveProgress.style.display = "block";
        liveStatus.textContent = "Starting upload job...";
        resultsBlock.style.display = "block";
        tableBody.innerHTML = "";

        try {
            const started = await startUploadJob(formData);
            if (!started.ok) {
                liveStatus.textContent = started.error;
                submitButton.disabled = false;
                cancelButton.style.display = "none";
                return;
            }

            localStorage.setItem(ACTIVE_JOB_KEY, started.jobId);
            startPolling(started.jobId);
        } catch (error) {
            liveStatus.textContent = `Upload failed to start: ${error}`;
            submitButton.disabled = false;
            cancelButton.style.display = "none";
        }
    });

    cancelButton.addEventListener("click", async function () {
        const jobId = localStorage.getItem(ACTIVE_JOB_KEY);
        if (!jobId) {
            return;
        }

        cancelButton.disabled = true;
        try {
            const canceled = await cancelUploadJob(jobId);
            liveStatus.textContent = canceled
                ? "Cancel requested. Stopping upload..."
                : "Unable to cancel upload job.";
        } catch (error) {
            liveStatus.textContent = `Cancel request failed: ${error}`;
        } finally {
            cancelButton.disabled = false;
        }
    });

    const existingJobId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (existingJobId) {
        liveProgress.style.display = "block";
        resultsBlock.style.display = "block";
        liveStatus.textContent = "Resuming upload progress...";
        submitButton.disabled = true;
        cancelButton.style.display = "inline-block";
        startPolling(existingJobId);
    }
})();
