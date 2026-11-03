"""Instance-scoped streaming adapters and HTTP transport; no global patches."""

from app.streaming.engine import streaming_session
__all__ = ['streaming_session']
