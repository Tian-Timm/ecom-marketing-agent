import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from src.web_api import (
    handle_template_background,
    handle_template_publish,
    handle_template_save,
    handle_template_test,
    handle_template_upload,
    handle_templates,
    send_json,
)


ROUTES = {
    "templates": ("GET", handle_templates),
    "background": ("GET", handle_template_background),
    "upload": ("POST", handle_template_upload),
    "save": ("POST", handle_template_save),
    "test": ("POST", handle_template_test),
    "publish": ("POST", handle_template_publish),
}


def dispatch(request: BaseHTTPRequestHandler, method: str) -> None:
    """Dispatch a rewritten template URL without mutating its original query."""
    action = str((parse_qs(urlparse(request.path).query).get("action") or [""])[0])
    route = ROUTES.get(action)
    if route is None:
        send_json(request, {"error": "not_found"}, HTTPStatus.NOT_FOUND)
        return
    allowed_method, target = route
    if method != allowed_method:
        send_json(
            request,
            {"error": "method_not_allowed"},
            HTTPStatus.METHOD_NOT_ALLOWED,
            headers={"Allow": allowed_method},
        )
        return
    target(request)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        dispatch(self, "GET")

    def do_POST(self) -> None:
        dispatch(self, "POST")

    def do_PUT(self) -> None:
        dispatch(self, "PUT")

    def do_PATCH(self) -> None:
        dispatch(self, "PATCH")

    def do_DELETE(self) -> None:
        dispatch(self, "DELETE")

    def do_OPTIONS(self) -> None:
        dispatch(self, "OPTIONS")
