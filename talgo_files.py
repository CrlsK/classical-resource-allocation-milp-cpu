"""
Emit additional-output FILES (not just JSON inside the result dict) for the
QCentroid platform's file viewer.

The platform exposes a per-executor presigned-URL endpoint:
    POST /v2/executors/{executor_id}/additional-output-file  body={"file_name": "<name>"}
    -> {"upload_url": "<presigned-S3-PUT-url>"}

The solver's runtime should expose:
    QCENTROID_API_URL   (e.g. https://api.dev.qcentroid.com)
    QCENTROID_TOKEN     (bearer token for the calling user/service)
    QCENTROID_EXECUTOR_ID  (numeric)

This helper:
1. Writes every file to ./additional_output/ AND ./output/ (best-effort fallbacks
   in case the platform tails a directory like the existing routing solvers do).
2. If the env vars are present, also POSTs to the API + PUTs the file to S3
   so the file appears under the job's additional-output viewer.
3. Logs what it does so a planner can audit what was uploaded.
"""
from __future__ import annotations
import json
import logging
import os
import urllib.error
import urllib.request
from typing import List

logger = logging.getLogger("qcentroid-user-log")

_PROBE_ENV_KEYS = (
    "QCENTROID_API_URL", "QCENTROID_API", "QC_API_URL",
    "QCENTROID_TOKEN", "QCENTROID_API_TOKEN", "QC_TOKEN", "ACCESS_TOKEN",
    "QCENTROID_EXECUTOR_ID", "EXECUTOR_ID", "QC_EXECUTOR_ID",
    "QCENTROID_JOB_NAME", "JOB_NAME", "QC_JOB_NAME",
)


def emit_files(files: List[dict]) -> dict:
    """
    files: list of {"name": "talgo_dashboard.html", "content": "<html>...", "content_type": "text/html"}

    Returns a small dict reporting where each file landed.
    """
    report = {"files": [], "env_probe": {k: ("set" if os.environ.get(k) else "unset") for k in _PROBE_ENV_KEYS}}

    # 1) write to local fallback dirs (best-effort)
    for d in ("additional_output", "output"):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

    for f in files:
        name = f["name"]
        content = f["content"]
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2, default=str)
        if isinstance(content, str):
            data_bytes = content.encode("utf-8")
        else:
            data_bytes = bytes(content)

        local_paths = []
        for d in ("additional_output", "output"):
            try:
                p = os.path.join(d, name)
                with open(p, "wb") as fh:
                    fh.write(data_bytes)
                local_paths.append(p)
            except Exception as e:
                logger.warning(f"failed to write {p}: {e}")

        # 2) If env vars present, also push to the platform via the API.
        api_url = (
            os.environ.get("QCENTROID_API_URL")
            or os.environ.get("QCENTROID_API")
            or os.environ.get("QC_API_URL")
        )
        token = (
            os.environ.get("QCENTROID_TOKEN")
            or os.environ.get("QCENTROID_API_TOKEN")
            or os.environ.get("QC_TOKEN")
            or os.environ.get("ACCESS_TOKEN")
        )
        executor_id = (
            os.environ.get("QCENTROID_EXECUTOR_ID")
            or os.environ.get("EXECUTOR_ID")
            or os.environ.get("QC_EXECUTOR_ID")
        )
        upload_status = "skipped (env vars not set)"
        if api_url and token and executor_id:
            try:
                req = urllib.request.Request(
                    f"{api_url.rstrip('/')}/v2/executors/{executor_id}/additional-output-file",
                    data=json.dumps({"file_name": name}).encode(),
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    body = json.loads(r.read().decode())
                up_url = body.get("upload_url")
                if up_url:
                    put = urllib.request.Request(up_url, data=data_bytes, method="PUT")
                    with urllib.request.urlopen(put, timeout=30) as r2:
                        upload_status = f"uploaded HTTP {r2.status}"
                else:
                    upload_status = f"no upload_url in response: {body}"
            except urllib.error.HTTPError as e:
                upload_status = f"HTTP {e.code}: {e.read().decode()[:180]}"
            except Exception as e:
                upload_status = f"error: {e}"

        logger.info(f"additional-output {name}: local={local_paths} upload={upload_status}")
        report["files"].append({"name": name, "local": local_paths, "upload": upload_status, "bytes": len(data_bytes)})
    return report
