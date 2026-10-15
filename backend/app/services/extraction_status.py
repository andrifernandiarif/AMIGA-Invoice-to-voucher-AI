"""Safe extraction progress for request owners; never return SDK errors or raw payloads."""

from app.models.extraction_job import ExtractionJobStatus

MAX_RETRIES = 3


def describe_extraction(job):
    if job is None:
        return None
    terminal_failure = job.status == ExtractionJobStatus.FAILED
    attempt = job.retry_count if terminal_failure else job.retry_count + 1
    attempt = min(attempt, MAX_RETRIES)
    retrying = job.status in (ExtractionJobStatus.PENDING, ExtractionJobStatus.IN_PROGRESS) and job.retry_count > 0
    message = None
    if retrying:
        message = (
            f"Retrying extraction automatically (attempt {attempt} of {MAX_RETRIES}). "
            "Your original uploaded documents are saved."
        )
    elif terminal_failure:
        message = (
            f"Extraction could not finish after {MAX_RETRIES} attempts. "
            "Your original uploaded documents are saved. Retry extraction to try again."
        )
    return {"status": job.status.value, "attempt": attempt, "max_attempts": MAX_RETRIES, "message": message}
