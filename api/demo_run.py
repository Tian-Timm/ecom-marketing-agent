import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from src.web_api import handle_demo_run


class handler(BaseHTTPRequestHandler):
    """公开 Demo POST；合规图片随结果以内联 data URL 返回。"""

    def do_POST(self) -> None:
        handle_demo_run(self)
