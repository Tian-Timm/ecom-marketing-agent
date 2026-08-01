from http.server import BaseHTTPRequestHandler

from src.web_api import handle_demo_run


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        handle_demo_run(self)
