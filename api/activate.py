from http.server import BaseHTTPRequestHandler
from src.web_api import handle_activate
class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None: handle_activate(self)
