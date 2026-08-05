export async function fetchUploadJob(jobId) {
    const response = await fetch(`/upload/jobs/${jobId}`);
    if (!response.ok) {
        return null;
    }
    return response.json();
}

export async function cancelUploadJob(jobId) {
    const response = await fetch(`/upload/jobs/${jobId}/cancel`, {
        method: "POST",
    });
    return response.ok;
}

export async function startUploadJob(formData) {
    const response = await fetch("/upload/jobs", {
        method: "POST",
        body: formData,
    });

    if (!response.ok) {
        const errorPayload = await response.json();
        return { ok: false, error: errorPayload.error || "Unable to start upload job." };
    }

    const payload = await response.json();
    if (!payload.job_id) {
        return { ok: false, error: "Upload job id was not returned." };
    }

    return { ok: true, jobId: payload.job_id };
}
