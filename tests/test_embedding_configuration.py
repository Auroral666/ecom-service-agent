from types import SimpleNamespace as NS
import pytest
from app.rag import configuration, embedder
from app.tools import knowledge


def config_values(**updates):
    values = dict(embedding_base_url='', embedding_api_key='', embedding_task_aware=False, embedding_model='test-model', openai_base_url='https://chat.example/v1', openai_api_key='CHAT_PRIVATE_KEY')
    values.update(updates)
    return NS(**values)


def test_local_embeddings_never_receive_chat_credentials(monkeypatch):
    monkeypatch.setattr(configuration, 'settings', config_values(embedding_base_url='http://127.0.0.1:18181/v1', embedding_task_aware=True))
    monkeypatch.setattr(configuration, 'Embedder', lambda **kwargs: kwargs)
    result = configuration.create_embedder()
    assert result['api_key'] == 'local' and result['task_aware'] is True


def test_different_remote_provider_requires_own_credentials(monkeypatch):
    monkeypatch.setattr(configuration, 'settings', config_values(embedding_base_url='https://embedding.example/v1'))
    with pytest.raises(ValueError, match='EMBEDDING_API_KEY'):
        configuration.create_embedder()


def test_legacy_same_provider_configuration_still_works(monkeypatch):
    monkeypatch.setattr(configuration, 'settings', config_values())
    monkeypatch.setattr(configuration, 'Embedder', lambda **kwargs: kwargs)
    result = configuration.create_embedder()
    assert result['base_url'] == 'https://chat.example/v1' and result['api_key'] == 'CHAT_PRIVATE_KEY'


def test_query_and_passage_use_their_own_jina_adapters(monkeypatch):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return NS(data=[NS(embedding=[0.1, 0.2]) for text in kwargs['input']])
    monkeypatch.setattr(embedder, 'OpenAI', lambda **kwargs: NS(embeddings=NS(create=create)))
    instance = embedder.Embedder('local', 'http://127.0.0.1:18181/v1', model='jina-embeddings-v3', task_aware=True)
    instance.encode(['document'])
    instance.encode_one('question')
    assert requests[0]['extra_body']['task'] == 'retrieval.passage'
    assert requests[1]['extra_body']['task'] == 'retrieval.query'
    plain = embedder.Embedder('key', 'https://embedding.example/v1')
    plain.encode_one('question')
    assert 'extra_body' not in requests[-1]


def test_failed_index_load_does_not_poison_singleton(monkeypatch):
    attempts = []
    class Retriever:
        def __init__(self, **kwargs): pass
        def load(self):
            attempts.append(1)
            if len(attempts) == 1: raise FileNotFoundError('index not built yet')
    monkeypatch.setattr(knowledge, '_retriever', None)
    monkeypatch.setattr(knowledge, 'create_embedder', lambda: object())
    monkeypatch.setattr(knowledge, '_create_backend_from_settings', lambda: object())
    monkeypatch.setattr(knowledge, 'KnowledgeRetriever', Retriever)
    with pytest.raises(FileNotFoundError): knowledge._get_retriever()
    assert knowledge._retriever is None
    assert knowledge._get_retriever() is knowledge._retriever


def test_embedding_query_failure_is_a_structured_tool_result(monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError('PRIVATE_CONNECTION_DETAILS')
    monkeypatch.setattr(knowledge, '_get_retriever', lambda: NS(search=fail))
    result = knowledge.search_knowledge('政策')
    assert result['success'] is False and result['error_code'] == 'EMBEDDING_QUERY_FAILED'
    assert 'PRIVATE_CONNECTION_DETAILS' not in str(result)
