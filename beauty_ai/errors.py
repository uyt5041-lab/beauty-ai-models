from __future__ import annotations

from uuid import UUID


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 provider_request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider_request_id = provider_request_id


class SubmissionUncertain(GatewayError):
    """A lost acknowledgement is not evidence that GPU execution never started."""
    def __init__(self, job_id: UUID):
        super().__init__("SUBMISSION_UNCERTAIN",
                         "Reconcile this job ID before considering resubmission.")
        self.job_id = job_id


class JobStillPending(GatewayError):
    def __init__(self, job_id: UUID):
        super().__init__("JOB_STILL_PENDING", "Waiting ended; the GPU job may still be running.")
        self.job_id = job_id
