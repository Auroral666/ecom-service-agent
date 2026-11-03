"""Authenticate existing chat-contract tests without bypassing route authorization."""
import json
from fastapi.testclient import TestClient
from fastapi import HTTPException


def authenticated_client(app, **kwargs):
    store = app.state.portal
    invitation = json.loads((store.root / 'invitations.json').read_text())['dev']
    try:
        user = store.register('regression_dev', '回归测试', 'dev', 'Test-only-password-2026', invitation)
    except HTTPException as error:
        assert error.status_code == 409
        user = store.login('regression_dev', 'Test-only-password-2026', 'dev')
    client = TestClient(app, **kwargs)
    client.headers['X-Portal-Request'] = '1'
    client.cookies.set('ecom_portal', store.new_login(user['id']), path='/api')
    return client
