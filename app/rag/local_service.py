"""Loopback-only OpenAI-compatible embeddings from an existing offline Jina cache.

Run in the existing ML environment; the chat environment does not need PyTorch.
"""
import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

MODEL_NAME = 'jina-embeddings-v3'
DEFAULT_PATH = r'D:\huggingface_cache\transformers\models--jinaai--jina-embeddings-v3\snapshots\ab036b023d30b4d1138c4c3bfa9f0c445ab455d6'
CODE_REVISION = '845308d0fd72a8406a3e378450e1a09522790419'


def create_handler(model, encode_lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

        def respond(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                self.respond(200, {'status': 'ok', 'model': MODEL_NAME, 'device': 'cpu', 'dimensions': 1024, 'offline': True})
            elif self.path == '/v1/models':
                self.respond(200, {'object': 'list', 'data': [{'id': MODEL_NAME, 'object': 'model', 'owned_by': 'local'}]})
            else:
                self.respond(404, {'error': {'message': 'Not found'}})

        def do_POST(self):
            if self.path != '/v1/embeddings':
                self.respond(404, {'error': {'message': 'Not found'}})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1_000_000:
                    raise ValueError('Request size must be between 1 and 1000000 bytes')
                payload = json.loads(self.rfile.read(size))
                if payload.get('model') != MODEL_NAME:
                    raise ValueError('Unknown embedding model')
                texts = payload.get('input')
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not 1 <= len(texts) <= 64 or any(not isinstance(text, str) or not text.strip() or len(text) > 10000 for text in texts):
                    raise ValueError('input must contain 1 to 64 non-empty strings, each at most 10000 characters')
                task = payload.get('task', 'retrieval.passage')
                if task not in {'retrieval.query', 'retrieval.passage'}:
                    raise ValueError('Unsupported retrieval task')
                with encode_lock:
                    vectors = model.encode(texts, task=task, batch_size=2, max_length=2048, normalize_embeddings=True)
                data = [{'object': 'embedding', 'index': i, 'embedding': vector.tolist()} for i, vector in enumerate(vectors)]
                self.respond(200, {'object': 'list', 'model': MODEL_NAME, 'data': data})
            except (ValueError, TypeError, KeyError):
                self.respond(422, {'error': {'message': 'Invalid embedding request'}})
            except Exception:
                logging.exception('Local embedding failed')
                self.respond(500, {'error': {'message': 'Local embedding failed; check local service logs'}})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', default=DEFAULT_PATH)
    parser.add_argument('--port', type=int, default=18181)
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args()
    model_path = Path(args.model_path).resolve()
    if not (model_path / 'model.safetensors').is_file():
        raise FileNotFoundError('A complete local model snapshot is required')
    cache = model_path.parents[3]
    os.environ.update(HF_HOME=str(cache), TRANSFORMERS_CACHE=str(cache / 'transformers'), HF_MODULES_CACHE=str(cache / 'modules'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    import torch
    from transformers import AutoModel
    torch.set_num_threads(max(1, min(args.threads, os.cpu_count() or 4)))
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    model = AutoModel.from_pretrained(str(model_path), trust_remote_code=True, local_files_only=True, code_revision=CODE_REVISION, cache_dir=str(cache / 'transformers'), use_flash_attn=False, torch_dtype=torch.float32).eval()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), create_handler(model, Lock()))
    logging.info('Offline embedding service ready: http://127.0.0.1:%s/v1 (%s)', args.port, MODEL_NAME)
    server.serve_forever()


if __name__ == '__main__':
    main()
