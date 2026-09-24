"""兼容原骨架的根入口：uvicorn main:app。"""

from app.main import app

__all__ = ["app"]
