from http.server import BaseHTTPRequestHandler

from src.web_api import handle_get


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        handle_get(self, "status")
