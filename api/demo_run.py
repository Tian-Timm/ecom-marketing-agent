from http.server import BaseHTTPRequestHandler

from src.web_api import handle_demo_run


class handler(BaseHTTPRequestHandler):
    """公开 Demo POST；合规图片随结果以内联 data URL 返回。"""

    def do_POST(self) -> None:
        handle_demo_run(self)
