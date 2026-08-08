import { ACTIVE_JOB_KEY, TERMINAL_JOB_STATUSES } from "../modules/constants.js";
import { cancelUploadJob, fetchUploadJob } from "../modules/uploadJobsApi.js";

(function initRecentUploadsColumnResizers() {
    const table = document.getElementById("recent-uploads-table");
    const handles = table ? Array.from(table.querySelectorAll(".column-resizer")) : [];
    if (!table || handles.length === 0) {
        return;
    }

    const minPx = 90;
    const maxPercent = 70;
    let startX = 0;
    let startWidth = 0;
    let activeCol = null;

    const clamp = function (value, min, max) {
        return Math.max(min, Math.min(max, value));
    };

    const onMouseMove = function (event) {
        if (!activeCol) {
            return;
        }

        const tableWidth = table.getBoundingClientRect().width;
        const maxPx = tableWidth * (maxPercent / 100);
        const nextWidth = clamp(startWidth + (event.clientX - startX), minPx, maxPx);
        activeCol.style.width = `${nextWidth}px`;
    };

    const onMouseUp = function () {
        activeCol = null;
        document.body.classList.remove("col-resize-active");
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
    };

    handles.forEach(function (handle) {
        handle.addEventListener("mousedown", function (event) {
            const colIndex = Number(handle.getAttribute("data-col-index"));
            const cols = table.querySelectorAll("colgroup col");
            const col = cols.item(colIndex);
            if (!col) {
                return;
            }

            event.preventDefault();
            activeCol = col;
            startX = event.clientX;
            startWidth = col.getBoundingClientRect().width || 0;
            document.body.classList.add("col-resize-active");
            window.addEventListener("mousemove", onMouseMove);
            window.addEventListener("mouseup", onMouseUp);
        });
    });
})();

(function initThemeToggle() {
    if (window.__balucThemeToggleBound) {
        return;
    }

    const toggle = document.getElementById("theme-toggle");
    const modeBadge = document.getElementById("theme-mode-badge");
    if (!toggle) {
        return;
    }

    const root = document.documentElement;
    const MODE_KEY = "baluc-theme-mode";
    const LEGACY_THEME_KEY = "baluc-theme";
    const modes = ["auto", "light", "dark"];
    const mediaQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

    const resolveTheme = function (mode) {
        if (mode === "auto") {
            return mediaQuery && mediaQuery.matches ? "dark" : "light";
        }
        return mode;
    };

    const updateLabel = function (mode) {
        const prettyMode = mode.charAt(0).toUpperCase() + mode.slice(1);
        toggle.textContent = `Theme: ${prettyMode}`;
        toggle.setAttribute("aria-label", `Change theme mode. Current mode is ${prettyMode}.`);
        if (modeBadge) {
            modeBadge.textContent = `Mode: ${prettyMode}`;
            modeBadge.setAttribute("data-mode", mode);
        }
    };

    const applyMode = function (mode, persist) {
        const theme = resolveTheme(mode);
        root.setAttribute("data-theme-mode", mode);
        root.setAttribute("data-theme", theme);
        updateLabel(mode);

        if (persist) {
            localStorage.setItem(MODE_KEY, mode);
            if (mode === "light" || mode === "dark") {
                localStorage.setItem(LEGACY_THEME_KEY, mode);
            } else {
                localStorage.removeItem(LEGACY_THEME_KEY);
            }
        }
    };

    const currentMode = root.getAttribute("data-theme-mode") || "auto";
    applyMode(currentMode, false);

    toggle.addEventListener("click", function () {
        const mode = root.getAttribute("data-theme-mode") || "auto";
        const index = modes.indexOf(mode);
        const nextMode = modes[(index + 1) % modes.length];
        applyMode(nextMode, true);
    });

    if (mediaQuery && typeof mediaQuery.addEventListener === "function") {
        mediaQuery.addEventListener("change", function () {
            const mode = root.getAttribute("data-theme-mode") || "auto";
            if (mode === "auto") {
                applyMode("auto", false);
            }
        });
    }

    window.__balucThemeToggleBound = true;
})();

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
