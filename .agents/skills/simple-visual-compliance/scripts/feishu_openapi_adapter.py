#!/usr/bin/env python3
"""面向线上运行环境的飞书 OpenAPI 适配器。"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List

FEISHU_API = "https://open.feishu.cn/open-apis"


def _safe_urlopen(request: urllib.request.Request, timeout: float = 15.0) -> Any:
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(request, timeout=timeout, context=ctx)


class FeishuOpenApiError(RuntimeError):
    pass


class FeishuOpenApiAdapter:
    """用应用身份提供与本地 CLI 适配器相同的飞书接口。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        api_base: str = FEISHU_API,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("飞书应用凭证未配置")
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._tenant_token: str | None = None

    @classmethod
    def from_env(cls) -> "FeishuOpenApiAdapter | None":
        app_id = os.environ.get("FEISHU_APP_ID", "").strip()
        app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
        if not app_id or not app_secret:
            return None
        return cls(
            app_id,
            app_secret,
            api_base=os.environ.get("FEISHU_API_BASE", FEISHU_API).strip()
            or FEISHU_API,
        )

    def _decode(self, response: Any) -> Dict[str, Any]:
        payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code", 0) != 0:
            raise FeishuOpenApiError(
                f"飞书接口失败 {payload.get('code')}: {payload.get('msg')}"
            )
        return payload

    def _token(self) -> str:
        if self._tenant_token:
            return self._tenant_token
        body = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}/auth/v3/tenant_access_token/internal/",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with _safe_urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = self._decode(response)
        except urllib.error.HTTPError as exc:
            raise FeishuOpenApiError(f"飞书鉴权失败: HTTP {exc.code}") from exc
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            raise FeishuOpenApiError("飞书鉴权成功但未返回访问凭证")
        self._tenant_token = token
        return token

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        body: Dict[str, Any] | None = None,
        query: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None if body is None else json.dumps(
            body,
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method=method,
        )
        try:
            with _safe_urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                return self._decode(response)
        except urllib.error.HTTPError as exc:
            raise FeishuOpenApiError(
                f"飞书接口请求失败: HTTP {exc.code}"
            ) from exc

    def list_records(self, base_token: str, table_id: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        page_token = ""
        while True:
            query: Dict[str, Any] = {"page_size": 500}
            if page_token:
                query["page_token"] = page_token
            payload = self._json_request(
                "GET",
                f"/bitable/v1/apps/{base_token}/tables/{table_id}/records",
                query=query,
            )
            data = payload.get("data") or {}
            for item in data.get("items") or []:
                record = dict(item.get("fields") or {})
                record["_record_id"] = item.get("record_id")
                records.append(record)
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                raise FeishuOpenApiError("飞书分页响应缺少 page_token")
        return records

    @staticmethod
    def _multipart_body(fields: Dict[str, str], file_path: Path) -> tuple[bytes, str]:
        boundary = f"----cha-cup-{uuid.uuid4().hex}"
        chunks: List[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                ).encode(),
                value.encode("utf-8"),
                b"\r\n",
            ])
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: image/png\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        return b"".join(chunks), boundary

    def upload_image(self, file_path: Path, task_id: str) -> Dict[str, Any]:
        body, boundary = self._multipart_body(
            {
                "file_name": f"CHA CUP {task_id} 营销图片.png",
                "parent_type": "bitable_file",
                "parent_node": os.environ.get(
                    "FEISHU_BASE_TOKEN",
                    "SfsSb7Tw2aeiQJsmQTlczjX7nyN",
                ),
                "size": str(file_path.stat().st_size),
            },
            file_path,
        )
        request = urllib.request.Request(
            f"{self.api_base}/drive/v1/medias/upload_all",
            data=body,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with _safe_urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = self._decode(response)
        except urllib.error.HTTPError as exc:
            raise FeishuOpenApiError(
                f"飞书图片上传失败: HTTP {exc.code}"
            ) from exc
        file_token = str((payload.get("data") or {}).get("file_token") or "")
        if not file_token:
            raise FeishuOpenApiError("飞书图片上传成功但未返回 file_token")
        return {
            "url": None,
            "attachment": [{"file_token": file_token}],
            "file_token": file_token,
        }

    def batch_update(
        self,
        base_token: str,
        table_id: str,
        updates: Dict[str, Dict[str, Any]],
    ) -> None:
        records = [
            {"record_id": record_id, "fields": fields}
            for record_id, fields in updates.items()
        ]
        self._json_request(
            "POST",
            (
                f"/bitable/v1/apps/{base_token}/tables/{table_id}"
                "/records/batch_update"
            ),
            body={"records": records},
        )

    def download_media(self, file_token: str) -> tuple[bytes, str]:
        safe_token = urllib.parse.quote(file_token, safe="")
        request = urllib.request.Request(
            f"{self.api_base}/drive/v1/medias/{safe_token}/download",
            headers={"Authorization": f"Bearer {self._token()}"},
            method="GET",
        )
        try:
            with _safe_urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                return (
                    response.read(),
                    response.headers.get("Content-Type", "image/png"),
                )
        except urllib.error.HTTPError as exc:
            raise FeishuOpenApiError(
                f"飞书图片读取失败: HTTP {exc.code}"
            ) from exc
