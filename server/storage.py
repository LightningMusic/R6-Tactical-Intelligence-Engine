import shutil
import hashlib
from pathlib import Path
from typing import BinaryIO, Tuple

from server.config import server_settings


class StorageManager:
    """
    Manages physical file storage on the server:
    1. Streaming upload handling with size enforcement.
    2. Atomic file storage in server_data/uploads/<hash>.r6session.
    3. Isolated work directories in server_data/work/<job_id>/.
    """

    @property
    def uploads_dir(self) -> Path:
        return server_settings.UPLOADS_DIR

    @property
    def work_dir(self) -> Path:
        return server_settings.WORK_DIR

    def stream_upload(self, input_stream: BinaryIO, filename: str) -> Tuple[Path, str, int]:
        """
        Streams uploaded file content into a temporary location while enforcing max size.
        Calculates SHA-256 hash incrementally.
        Returns: (temp_file_path, sha256_hash, file_size_bytes)
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.work_dir / f"upload_tmp_{hashlib.md5(filename.encode()).hexdigest()}.tmp"

        sha = hashlib.sha256()
        bytes_read = 0

        try:
            with temp_file.open("wb") as out:
                while chunk := input_stream.read(65536):
                    bytes_read += len(chunk)
                    if bytes_read > server_settings.MAX_UPLOAD_BYTES:
                        raise ValueError(
                            f"File size exceeds maximum allowed limit of {server_settings.MAX_UPLOAD_BYTES} bytes."
                        )
                    sha.update(chunk)
                    out.write(chunk)

            file_hash = sha.hexdigest().lower()
            return temp_file, file_hash, bytes_read

        except Exception:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            raise

    def store_permanent_archive(self, temp_file: Path, file_hash: str) -> Path:
        """
        Atomically moves temp upload file to server_data/uploads/<file_hash>.r6session.
        If file already exists, temp file is removed.
        """
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.uploads_dir / f"{file_hash}.r6session"

        if final_path.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
            return final_path

        temp_file.rename(final_path)
        return final_path

    def create_work_directory(self, job_id: str) -> Path:
        """Creates isolated work directory server_data/work/<job_id>."""
        job_work_dir = self.work_dir / job_id
        if job_work_dir.exists():
            shutil.rmtree(job_work_dir, ignore_errors=True)
        job_work_dir.mkdir(parents=True, exist_ok=True)
        return job_work_dir

    def cleanup_work_directory(self, job_id: str) -> None:
        """Cleans up isolated work directory after processing."""
        job_work_dir = self.work_dir / job_id
        if job_work_dir.exists():
            try:
                shutil.rmtree(job_work_dir, ignore_errors=True)
            except Exception as e:
                print(f"[StorageManager] Warning: failed to cleanup {job_work_dir}: {e}")


storage_manager = StorageManager()
