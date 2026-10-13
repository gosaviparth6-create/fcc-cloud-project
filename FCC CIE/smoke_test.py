"""Start Waitress locally and exercise the API over HTTP with local storage."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8000"
DATABASE_PATH = ROOT / "local.db"
UPLOAD_DIR = ROOT / "uploads"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    label = "PASS" if passed else "FAIL"
    print(f"{label}: {name}" + (f" — {detail}" if detail else ""), flush=True)


def http_request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        urllib.parse.urljoin(BASE_URL, path),
        data=body,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def json_request(
    method: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    status, response = http_request(method, path, body=body, headers=headers)
    try:
        return status, json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {}


def multipart_file(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"smoke-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"


def db_row(submission_id: str):
    with sqlite3.connect(DATABASE_PATH, timeout=5) as connection:
        return connection.execute(
            "SELECT id, upload_key, original_filename FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()


def main() -> int:
    # Use test-only values in the child process. Disable dotenv and strip AWS
    # credentials so this run neither reads local secrets nor contacts AWS.
    child_env = os.environ.copy()
    for name in tuple(child_env):
        if name.startswith("AWS_"):
            child_env.pop(name, None)
    child_env.update({
        "USE_SSM": "false",
        "PYTHON_DOTENV_DISABLED": "true",
        "STORAGE_BACKEND": "local",
        "DATABASE_URL": "sqlite:///local.db",
        "ADMIN_USERNAME": "smoke-admin",
        "ADMIN_PASSWORD": "smoke-test-only-password",
        "SECRET_KEY": "smoke-test-only-signing-key-not-a-real-secret",
        "S3_BUCKET": "smoke-test-placeholder-bucket",
        "AWS_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "ALLOWED_ORIGIN": "http://localhost:3000",
        "PORT": "8000",
    })

    server_code = (
        "from waitress import serve; from app import app; "
        "serve(app, listen='127.0.0.1:8000')"
    )
    server = subprocess.Popen(
        [sys.executable, "-c", server_code],
        cwd=ROOT,
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    contact_id = None
    upload_submission_id = None
    upload_key = None
    token = None
    try:
        started = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if server.poll() is not None:
                break
            try:
                status, _ = http_request("GET", "/api/health")
                if status == 200:
                    started = True
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        record("Waitress starts and accepts HTTP requests", started)
        if not started:
            if server.poll() is not None:
                print(f"Waitress exited with status {server.returncode}")
            return 1

        status, login = json_request(
            "POST",
            "/api/admin/login",
            {"username": "smoke-admin", "password": "smoke-test-only-password"},
        )
        token = login.get("token")
        record("Admin login obtains a smoke-test token", status == 200 and bool(token))

        status, contact = json_request(
            "POST",
            "/api/contact",
            {"name": "Smoke Test", "email": "smoke@example.invalid", "message": "HTTP smoke test"},
        )
        contact_id = contact.get("id")
        contact_row = db_row(contact_id) if contact_id else None
        record(
            "Valid contact JSON creates a database row",
            status == 201 and bool(contact_id) and contact_row is not None,
            f"HTTP {status}",
        )

        pdf_bytes = b"%PDF-1.4\n% smoke test\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
        file_body, content_type = multipart_file("Smoke resume.pdf", pdf_bytes)
        status, response = http_request(
            "POST", "/api/upload-doc", body=file_body,
            headers={"Content-Type": content_type},
        )
        try:
            upload_result = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            upload_result = {}
        upload_key = upload_result.get("upload_key")
        key_is_uuid = bool(
            isinstance(upload_key, str)
            and re.fullmatch(r"uploads/[0-9a-fA-F-]{36}\.pdf", upload_key)
        )
        upload_path = ROOT / upload_key if key_is_uuid else None
        file_written = bool(upload_path and upload_path.is_file() and upload_path.read_bytes() == pdf_bytes)
        record(
            "Valid PDF upload succeeds and stores a UUID-named file",
            status == 201 and key_is_uuid and file_written,
            f"HTTP {status}, key={upload_key!r}",
        )

        if status == 201 and upload_key:
            link_status, linked = json_request(
                "POST",
                "/api/contact",
                {
                    "name": "Smoke Upload",
                    "email": "smoke-upload@example.invalid",
                    "message": "Upload metadata association",
                    "has_upload": True,
                    "upload_key": upload_key,
                    "original_filename": "Smoke resume.pdf",
                },
            )
            upload_submission_id = linked.get("id")
            stored_row = db_row(upload_submission_id) if upload_submission_id else None
            record(
                "Uploaded key and original filename are saved on the DB row",
                link_status == 201
                and stored_row is not None
                and stored_row[1] == upload_key
                and stored_row[2] == "Smoke resume.pdf",
                f"HTTP {link_status}, row={stored_row!r}",
            )
        else:
            record("Uploaded key and original filename are saved on the DB row", False, "upload failed")

        bad_body, bad_type = multipart_file("renamed.txt.pdf", b"plain text, not a PDF\n")
        bad_status, _ = http_request(
            "POST", "/api/upload-doc", body=bad_body,
            headers={"Content-Type": bad_type},
        )
        record("PDF extension with non-PDF content is rejected", bad_status == 400, f"HTTP {bad_status}")

        large_body, large_type = multipart_file(
            "large.pdf", b"%PDF-" + b"x" * MAX_UPLOAD_SIZE
        )
        large_status, _ = http_request(
            "POST", "/api/upload-doc", body=large_body,
            headers={"Content-Type": large_type},
        )
        record("Upload over 10 MB is rejected", large_status == 413, f"HTTP {large_status}")

        no_file_status, _ = http_request("POST", "/api/upload-doc")
        record("Upload request with no file is rejected", no_file_status == 400, f"HTTP {no_file_status}")

        list_status, listing = json_request("GET", "/api/admin/submissions", token=token or "")
        submissions = listing.get("submissions", [])
        listed_upload = next(
            (item for item in submissions if item.get("id") == upload_submission_id), None
        )
        record(
            "Authenticated admin list includes the test submissions",
            list_status == 200
            and any(item.get("id") == contact_id for item in submissions)
            and listed_upload is not None,
            f"HTTP {list_status}",
        )

        download_url = listed_upload.get("download_url") if listed_upload else None
        if download_url and token:
            download_status, download_bytes = http_request(
                "GET",
                urllib.parse.urljoin(BASE_URL, download_url),
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            download_status, download_bytes = 0, b""
        record(
            "Returned local file URL downloads the uploaded PDF",
            download_status == 200 and download_bytes == pdf_bytes,
            f"HTTP {download_status}",
        )

        if upload_submission_id and token:
            delete_status, delete_response = json_request(
                "DELETE", f"/api/admin/submissions/{upload_submission_id}", token=token
            )
            row_removed = db_row(upload_submission_id) is None
            file_removed = bool(upload_path and not upload_path.exists())
        else:
            delete_status, delete_response = 0, {}
            row_removed = file_removed = False
        record(
            "Deleting the submission removes its DB row and file",
            delete_status == 200 and row_removed and file_removed,
            f"HTTP {delete_status}, row_removed={row_removed}, file_removed={file_removed}",
        )
        upload_submission_id = None if row_removed else upload_submission_id
        upload_key = None if file_removed else upload_key

        tampered_status, _ = json_request(
            "GET", "/api/admin/submissions", token="tampered-token"
        )
        record("Tampered admin token returns 401", tampered_status == 401, f"HTTP {tampered_status}")

        bad_login_statuses = []
        for _ in range(6):
            bad_status, _ = json_request(
                "POST",
                "/api/admin/login",
                {"username": "smoke-admin", "password": "incorrect-smoke-password"},
            )
            bad_login_statuses.append(bad_status)
        record(
            "Six rapid bad logins trigger HTTP 429",
            429 in bad_login_statuses,
            f"statuses={bad_login_statuses}",
        )

    except Exception as error:
        record("Smoke test execution", False, f"{type(error).__name__}: {error}")
    finally:
        # Remove any test artifacts left behind when a check fails.
        if token and contact_id:
            try:
                json_request("DELETE", f"/api/admin/submissions/{contact_id}", token=token)
            except Exception:
                pass
        if token and upload_submission_id:
            try:
                json_request("DELETE", f"/api/admin/submissions/{upload_submission_id}", token=token)
            except Exception:
                pass
        if upload_key and re.fullmatch(r"uploads/[0-9a-fA-F-]{36}\.pdf", upload_key):
            (ROOT / upload_key).unlink(missing_ok=True)
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    failures = sum(not passed for _, passed, _ in RESULTS)
    print(f"\nSmoke test summary: {len(RESULTS) - failures} passed, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
