"""Embedding settings are independent of chat-model credentials and endpoint."""
from urllib.parse import urlsplit

from app.config.settings import settings
from app.rag.embedder import Embedder


def create_embedder():
    base_url = settings.embedding_base_url or settings.openai_base_url
    api_key = settings.embedding_api_key
    if not api_key:
        if not settings.embedding_base_url:
            api_key = settings.openai_api_key
        elif urlsplit(base_url).hostname in {'127.0.0.1', 'localhost', '::1'}:
            api_key = 'local'
        else:
            raise ValueError('请配置独立的 EMBEDDING_API_KEY，不会将聊天密钥发送到其他服务')
    return Embedder(api_key=api_key, base_url=base_url, model=settings.embedding_model, task_aware=settings.embedding_task_aware)
