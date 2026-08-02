import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from src.web_api import handle_get


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        handle_get(self, "sources")
