"""Streaming at model/tool boundaries without changing the existing ReAct loop."""
import json
from contextlib import contextmanager
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from jiter import from_json
from openai.types.chat import ChatCompletion
from openai import BadRequestError

from app.schemas.response import CustomerServiceResponse
from app.streaming.results import LABELS, business_result
from app.streaming.security import redact


class StreamingRun:
    def __init__(self, agent, emit):
        self.agent = agent
        self.emit = emit
        self.client = agent.client
        self.round = 0
        self.answer = ''
        self.answer_streamed = False
        self.pending_tools = {}

    def model_completion(self, client, kwargs, phase='react', on_content=None):
        started = perf_counter()
        model_id = uuid4().hex
        if phase == 'react':
            self.round += 1
        self.emit({'type': 'model', 'id': model_id, 'state': 'running', 'phase': phase, 'round': self.round, 'model': kwargs.get('model'), 'messages': redact(kwargs.get('messages', []))})
        self.emit({'type': 'status', 'text': '正在整理回复信息…' if phase == 'metadata' else '正在生成回复…' if phase == 'answer' else '正在处理您的咨询…'})
        text, calls, prepared = '', {}, set()
        response_id, usage, finish = model_id, None, 'stop'
        try:
            stream = client.chat.completions.create(**kwargs, stream=True, stream_options={'include_usage': True})
        except BadRequestError as exc:
            if 'stream_options' not in str(exc):
                raise
            stream = client.chat.completions.create(**kwargs, stream=True)
        try:
            for chunk in stream:
                response_id = chunk.id or response_id
                if chunk.usage is not None:
                    usage = chunk.usage.model_dump()
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    if phase == 'react':
                        self.emit({'type': 'response_delta', 'id': model_id, 'text': delta.content})
                    if on_content:
                        on_content(text)
                # Provider reasoning_content is intentionally never forwarded.
                for tool in delta.tool_calls or []:
                    call = calls.setdefault(tool.index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    call['id'] += tool.id or ''
                    if tool.function:
                        call['function']['name'] += tool.function.name or ''
                        call['function']['arguments'] += tool.function.arguments or ''
                    name = call['function']['name']
                    if name in LABELS and tool.index not in prepared:
                        prepared.add(tool.index)
                        self.emit({'type': 'tool', 'id': f'{model_id}-{tool.index}', 'name': name, 'label': LABELS[name], 'state': 'waiting', 'summary': '准备' + LABELS[name] + '…'})
                if choice.finish_reason:
                    finish = choice.finish_reason
        finally:
            stream.close()
        if phase == 'react' and text:
            kind = 'activity' if calls else 'answer'
            if not calls:
                self.answer = text
                self.answer_streamed = True
            self.emit({'type': 'response_end', 'id': model_id, 'kind': kind, 'text': text})
        for index, call in calls.items():
            name = call['function']['name']
            call_id = f'{model_id}-{index}'
            self.pending_tools.setdefault(name, []).append(call_id)
            if index not in prepared:
                self.emit({'type': 'tool', 'id': call_id, 'name': name, 'label': LABELS.get(name, name), 'state': 'waiting', 'summary': '准备调用…'})
        self.emit({'type': 'model', 'id': model_id, 'state': 'succeeded', 'phase': phase, 'round': self.round, 'model': kwargs.get('model'), 'elapsed_ms': round((perf_counter() - started) * 1000), 'usage': usage})
        return ChatCompletion.model_validate({'id': response_id, 'object': 'chat.completion', 'created': 0, 'model': kwargs.get('model', ''), 'choices': [{'index': 0, 'finish_reason': finish, 'message': {'role': 'assistant', 'content': text, 'tool_calls': list(calls.values()) or None}}], 'usage': usage})

    def final_reply(self, text):
        """Finalize metadata without regenerating or replacing streamed answer text."""
        if self.answer_streamed:
            if text != self.answer:
                raise ValueError('Agent final answer differs from the streamed answer')
            schema = CustomerServiceResponse.model_json_schema()
            schema['properties'].pop('reply', None)
            schema['required'] = [key for key in schema.get('required', []) if key != 'reply']
            prompt = ('根据客服回复仅提取意图、置信度、是否需要人工和追问。只输出符合下列结构的 JSON，'
                      '不要输出 reply 字段，不要重写正文：' + json.dumps(schema, ensure_ascii=False))
            response = self.model_completion(self.client, {'model': self.agent.model, 'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': text}], 'temperature': 0.0}, phase='metadata')
            raw = response.choices[0].message.content.strip()
            if raw.startswith('```'):
                raw = raw.partition('\n')[2].rsplit('```', 1)[0].strip()
            metadata = json.loads(raw)
            metadata['reply'] = text
            return CustomerServiceResponse.model_validate(metadata)
        schema = CustomerServiceResponse.model_json_schema()
        prompt = ('根据客服回复提取结构化信息，只输出一个 JSON 对象。reply 字段放在第一位，忠实保留事实和结论，不增加事实。'
                  '如果已有工具结果展示订单、物流或商品卡片，reply 可简短说明并引用卡片，避免重复粘贴同一份大表格。'
                  '不要声称卡片或订单行可点击，不声称已执行工具结果未确认的操作。'
                  '必须遵守以下 JSON Schema：' + json.dumps(schema, ensure_ascii=False))

        def on_content(buffer):
            candidate = buffer.lstrip()
            if candidate.startswith('```'):
                candidate = candidate.partition('\n')[2]
            try:
                partial = from_json(candidate.encode('utf-8'), partial_mode='trailing-strings')
            except ValueError:
                return
            reply = partial.get('reply') if isinstance(partial, dict) else None
            if isinstance(reply, str) and reply.startswith(self.answer):
                delta = reply[len(self.answer):]
                if delta:
                    self.answer = reply
                    self.emit({'type': 'delta', 'text': delta})

        response = self.model_completion(self.client, {'model': self.agent.model, 'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': text}], 'temperature': 0.0}, phase='answer', on_content=on_content)
        raw = response.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.partition('\n')[2].rsplit('```', 1)[0].strip()
        result = CustomerServiceResponse.model_validate_json(raw)
        if not result.reply.startswith(self.answer):
            raise ValueError('Final reply changed after streaming')
        if result.reply != self.answer:
            self.emit({'type': 'delta', 'text': result.reply[len(self.answer):]})
        self.answer = result.reply
        return result


class ClientAdapter:
    def __init__(self, client, run, phase='react'):
        self._client, self._run, self._phase = client, run, phase
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __getattr__(self, key):
        return getattr(self._client, key)

    def create(self, **kwargs):
        return self._run.model_completion(self._client, kwargs, phase=self._phase)


class ToolAdapter:
    def __init__(self, tools, run):
        self._tools, self._run = tools, run

    def __getattr__(self, key):
        return getattr(self._tools, key)

    def execute_tool(self, name, arguments):
        pending = self._run.pending_tools.get(name, [])
        call_id = pending.pop(0) if pending else uuid4().hex
        source = self._tools.source_for(name)
        started = perf_counter()
        base = {'type': 'tool', 'id': call_id, 'name': name, 'label': LABELS.get(name, name), 'source': source, 'args': redact(arguments)}
        self._run.emit({**base, 'state': 'running', 'summary': '正在' + LABELS.get(name, name) + '…'})
        try:
            raw = self._tools.execute_tool(name, arguments)
            view = business_result(name, raw)
        except Exception:
            self._run.emit({**base, 'state': 'failed', 'elapsed_ms': round((perf_counter() - started) * 1000), 'summary': '工具调用失败', 'error': '执行异常，请在本机服务日志中查看原因。'})
            raise
        if view.get('label'):
            base['label'] = view['label']
        self._run.emit({**base, 'state': 'succeeded' if view['success'] else 'failed', 'elapsed_ms': round((perf_counter() - started) * 1000), 'summary': view['summary'], 'error': view['error'], 'result': redact(raw)})
        if view['block']:
            self._run.emit({'type': 'business', 'block': {**view['block'], 'id': call_id}})
        return raw


@contextmanager
def streaming_session(agent, emit=None):
    """Adapters exist only for this serialized request and restore on every exit."""
    if emit is None:
        yield None
        return
    run = StreamingRun(agent, emit)
    owners = [agent, *getattr(agent, 'agents', {}).values()]
    if hasattr(agent, 'router'):
        owners.append(agent.router)
    previous = []
    try:
        for owner in owners:
            for attr, adapter in [('client', ClientAdapter), ('tool_manager', ToolAdapter)]:
                if hasattr(owner, attr):
                    original = getattr(owner, attr)
                    previous.append((owner, attr, original))
                    adapted = adapter(original, run, phase='route') if adapter is ClientAdapter and owner is getattr(agent, 'router', None) else adapter(original, run)
                    setattr(owner, attr, adapted)
        yield run
    finally:
        for owner, attr, original in reversed(previous):
            setattr(owner, attr, original)
