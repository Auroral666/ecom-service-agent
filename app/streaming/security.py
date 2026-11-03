"""Redaction shared by public events and the protected local debug view."""
import re

SENSITIVE = re.compile(r'api.?key|token|secret|password|authorization|cookie|phone|mobile|address|身份证|手机号|地址|密码', re.I)
SECRET = re.compile(r'(?i)(?:sk-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._-]+)')


def public_event(event):
    """The same visibility filter is used for live events and saved presentation."""
    if event['type'] in {'model', 'memory'}:
        return None
    if event['type'] == 'tool':
        public = {k: v for k, v in event.items() if k not in {'args', 'result'}}
        if public.get('error'):
            public['error'] = '知识资料暂不可用，本次尚无法核实政策。' if public.get('name') == 'search_knowledge' else '本次业务查询未完成，请核对信息后重试；详细原因可在执行调试中查看。'
        return public
    return event


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[已脱敏]' if SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(('{', '[')):
            import json
            try:
                return json.dumps(redact(json.loads(value)), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        text = SECRET.sub('[已脱敏]', value)
        text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[手机号已脱敏]', text)
        text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已脱敏]', text)
        text = re.sub(r'(?i)((?:api[_-]?key|password|secret|authorization|token)\s*[=:]\s*)[^\s,;\"\'}]+', r'\1[已脱敏]', text)
        return text
    return value


def safe_steps(steps):
    """Legacy traces must not expose model reasoning or raw customer records."""
    result = []
    for step in steps or []:
        item = step.model_dump() if hasattr(step, 'model_dump') else dict(step)
        if item.get('type') == 'thought':
            continue
        # Customer history keeps presentation data, not the debug parameters.
        item['tool_args'] = None
        raw = item.get('tool_result')
        if raw:
            import json
            from app.streaming.results import business_result
            view = business_result(item.get('tool_name'), raw)
            block = view['block']
            mapping = {'orders': 'orders', 'logistics': 'logistics', 'products': 'products', 'sources': 'results'}
            if block and block['kind'] in mapping:
                item['tool_result'] = json.dumps({'success': view['success'], mapping[block['kind']]: block['data']}, ensure_ascii=False)
            elif block and block['kind'] == 'refund':
                item['tool_result'] = json.dumps(redact(block['data']), ensure_ascii=False)
            else:
                item['tool_result'] = None
            item['content'] = view['summary']
        result.append(redact(item))
    return result
