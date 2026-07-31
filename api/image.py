from http.server import BaseHTTPRequestHandler

from src.web_api import handle_image


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        handle_image(self)
