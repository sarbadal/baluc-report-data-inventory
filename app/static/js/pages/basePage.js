import { ACTIVE_JOB_KEY, TERMINAL_JOB_STATUSES } from "../modules/constants.js";
import { cancelUploadJob, fetchUploadJob } from "../modules/uploadJobsApi.js";

(function initGlobalUploadTracker() {
    const tracker = document.getElementById("global-upload-tracker");
    const statusText = document.getElementById("global-upload-status");
    const cancelButton = document.getElementById("global-upload-cancel");
    if (!tracker || !statusText || !cancelButton) {
        return;
    }

    const jobId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (!jobId) {
        return;
    }

    tracker.style.display = "flex";

    cancelButton.addEventListener("click", async function () {
        const activeJobId = localStorage.getItem(ACTIVE_JOB_KEY);
        if (!activeJobId) {
            return;
        }

        cancelButton.disabled = true;
        try {
            const canceled = await cancelUploadJob(activeJobId);
            statusText.textContent = canceled
                ? "Cancel requested. Stopping upload..."
                : "Unable to cancel upload.";
        } catch (error) {
            statusText.textContent = `Cancel request failed: ${error}`;
        } finally {
            cancelButton.disabled = false;
        }
    });

    const pollHandle = setInterval(async function () {
        const job = await fetchUploadJob(jobId);
        if (!job) {
            statusText.textContent = "Upload status unavailable.";
            clearInterval(pollHandle);
            localStorage.removeItem(ACTIVE_JOB_KEY);
            return;
        }

        statusText.textContent = `${job.status.toUpperCase()}: ${job.processed_files}/${job.total_files} | ${job.current_step}`;

        if (TERMINAL_JOB_STATUSES.has(job.status)) {
            clearInterval(pollHandle);
            localStorage.removeItem(ACTIVE_JOB_KEY);
            tracker.style.display = "none";
        }
    }, 1500);
})();
