import { ACTIVE_JOB_KEY, TERMINAL_JOB_STATUSES } from "../modules/constants.js";
import { fetchUploadJob } from "../modules/uploadJobsApi.js";

(function initHomePageUploadStatus() {
    const statusEl = document.getElementById("home-upload-status");
    if (!statusEl) {
        return;
    }

    const jobId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (!jobId) {
        statusEl.textContent = "No active upload";
        return;
    }

    statusEl.textContent = "Checking active upload...";

    const poll = async function () {
        try {
            const job = await fetchUploadJob(jobId);
            if (!job) {
                statusEl.textContent = "Upload status unavailable";
                localStorage.removeItem(ACTIVE_JOB_KEY);
                return true;
            }

            statusEl.textContent = `${job.status.toUpperCase()} | ${job.processed_files}/${job.total_files}`;
            if (TERMINAL_JOB_STATUSES.has(job.status)) {
                localStorage.removeItem(ACTIVE_JOB_KEY);
                return true;
            }

            return false;
        } catch (error) {
            statusEl.textContent = `Status check failed: ${error}`;
            return true;
        }
    };

    poll().then(function (done) {
        if (done) {
            return;
        }

        const handle = setInterval(async function () {
            const finished = await poll();
            if (finished) {
                clearInterval(handle);
            }
        }, 1500);
    });
})();
