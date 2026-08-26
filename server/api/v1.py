import uuid
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi.responses import JSONResponse

from server.auth import verify_api_token
from server.repositories import ServerRepository
from server.storage import storage_manager
from server.services.package_validation import ServerPackageValidator
from server.config import server_settings

router = APIRouter(prefix="/api/v1")
repo = ServerRepository()


@router.get("/health")
def get_health() -> dict:
    """Unauthenticated health check endpoint."""
    return {
        "status": "ok",
        "service": "R6Analyzer Remote Server",
        "version": server_settings.ALLOWED_PACKAGE_VERSION,
    }


@router.get("/auth/test")
def test_auth(client_name: str = Depends(verify_api_token)) -> dict:
    """Authenticated auth test endpoint."""
    return {"status": "authenticated", "client": client_name}


@router.post("/sessions/upload")
def upload_session_package(
    file: UploadFile = File(...),
    client_name: str = Depends(verify_api_token),
) -> dict:
    """
    Authenticated upload endpoint for immutable .r6session archives.
    Streams upload content, validates package integrity, deduplicates, and enqueues processing job.
    """
    filename = file.filename or "upload.r6session"
    if not filename.endswith(".r6session") and not filename.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file extension. Expected .r6session archive.",
        )

    # 1. Stream upload and enforce size limits
    try:
        temp_file, file_hash, file_size = storage_manager.stream_upload(file.file, filename)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(val_err))
    except Exception as err:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Upload stream error: {err}")

    # 2. Validate package structure and manifest checksums
    valid, err_msg, manifest = ServerPackageValidator.validate_package(temp_file)
    if not valid:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Package validation failed: {err_msg}")

    session_id = str(manifest.get("session_id", ""))
    if not session_id:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Manifest missing session_id")

    is_complete = bool(manifest.get("is_complete", True))

    # 3. Duplicate handling policy
    existing_session = repo.get_session(session_id)
    existing_package = repo.get_package(file_hash)

    # Case 1: Same session ID + same package hash => idempotent success
    if existing_session and existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        job = repo.get_job_by_session(session_id)
        return {
            "session_id": session_id,
            "job_id": job["job_id"] if job else "unknown",
            "status": job["status"] if job else "completed",
            "is_duplicate": True,
            "message": "Session package already uploaded and registered.",
        }

    # Case 2: Same session ID + different package hash => Conflict 409
    if existing_session and not existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session ID {session_id} already exists with a different package content.",
        )

    # Case 3: Different session ID + same package hash => content duplicate
    if not existing_session and existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        orig_session_id = existing_package["session_id"]
        job = repo.get_job_by_session(orig_session_id)
        return {
            "session_id": orig_session_id,
            "job_id": job["job_id"] if job else "unknown",
            "status": job["status"] if job else "completed",
            "is_duplicate": True,
            "message": "Content duplicate package already exists under an existing session.",
        }

    # 4. Store archive permanently in server_data/uploads/<hash>.r6session
    final_archive = storage_manager.store_permanent_archive(temp_file, file_hash)

    # 5. Create database records and enqueue job
    repo.create_session(
        session_id=session_id,
        client_name=client_name,
        map_name="Pending",
    )
    repo.create_package(
        package_hash=file_hash,
        session_id=session_id,
        file_name=final_archive.name,
        file_size_bytes=file_size,
        is_complete=is_complete,
    )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = repo.create_job(job_id=job_id, session_id=session_id, package_hash=file_hash, initial_status="queued")

    return {
        "session_id": session_id,
        "job_id": job_id,
        "status": "queued",
        "is_duplicate": False,
        "message": "Package uploaded and queued for processing.",
    }


@router.get("/sessions")
def list_sessions(limit: int = 50, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint listing recent server sessions."""
    sessions = repo.list_sessions(limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
def get_session_details(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint fetching session details."""
    session = repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found.")

    job = repo.get_job_by_session(session_id)
    return {"session": session, "job": job}


@router.get("/sessions/{session_id}/status")
def get_session_status(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint fetching processing status for a session."""
    job = repo.get_job_by_session(session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job found for session {session_id}.")
    return {
        "session_id": session_id,
        "job_id": job["job_id"],
        "status": job["status"],
        "attempts": job["attempts"],
        "error_message": job["error_message"],
        "updated_at": job["updated_at"],
    }


@router.post("/sessions/{session_id}/retry")
def retry_failed_session_job(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint to re-queue a failed processing job."""
    job = repo.get_job_by_session(session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job found for session {session_id}.")

    if job["status"] not in ("failed", "interrupted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry job in status '{job['status']}'. Only failed jobs can be retried.",
        )

    repo.update_job_status(job_id=job["job_id"], status="queued", error_message=None)
    return {
        "session_id": session_id,
        "job_id": job["job_id"],
        "status": "queued",
        "message": "Job successfully re-queued for processing.",
    }


@router.get("/sessions/{session_id}/report")
def get_session_report(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Stubbed endpoint for report retrieval (deferred feature)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Report generation endpoint is deferred to a future milestone.",
    )


@router.get("/sessions/{session_id}/pdf")
def download_session_pdf(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Stubbed endpoint for PDF downloads (deferred feature)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="PDF export endpoint is deferred to a future milestone.",
    )
