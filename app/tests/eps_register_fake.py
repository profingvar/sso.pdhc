"""A fake eps for self-registration (#1049): it stands where
`eps_client._http_post` is, and nothing else is patched.

eps's answers as contract s7.2 fixes them:
    201 {"patient_guid"} + X-PDHC-Patient-Guid   created
    409 {"error": "exists"}                       the number is already there
    422 {"error": "unknown_unit"}                 not an active eps clinic
`mode` makes it misbehave the ways a real network or another vhost does.
"""
import uuid

import pytest
import requests

EPS_CFG = {'EPS_BASE_URL': 'http://eps.test', 'EPS_API_KEY': 'eps-test-key'}


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError('not json')
        return self._body


class FakeEpsRegister:
    def __init__(self):
        self.existing = set()     # personnummer eps already holds
        self.units = None         # None = every unit is an eps clinic
        self.created = {}         # personnummer -> patient guid
        self.mode, self.calls = 'ok', []
        self.guid_to_return = None   # force the guid eps names on a 201

    def __call__(self, url, headers, json_body, timeout):
        self.calls.append({'url': url, 'headers': dict(headers), 'json': dict(json_body)})
        m = self.mode
        if m == 'down':
            raise requests.ConnectionError('refused')
        if m == 'timeout':
            raise requests.Timeout('slow')
        if m in (401, 403, 500, 502):
            return _Resp(m, {'error': 'nope'})
        if m == 'foreign409':
            return _Resp(409, {'error': 'conflict', 'service': '1177.pdhc'})
        if m == 'foreign404':
            return _Resp(404, '<html>404</html>')
        if m == 'garbage201':
            return _Resp(201, {'patient_guid': 'not-a-guid'})
        pnr = json_body['personnummer']
        if self.units is not None and json_body['care_unit_guid'] not in self.units:
            return _Resp(422, {'error': 'unknown_unit'})
        if pnr in self.existing:
            return _Resp(409, {'error': 'exists'})
        guid = self.guid_to_return or str(uuid.uuid4())
        self.existing.add(pnr)
        self.created[pnr] = guid
        header = str(uuid.uuid4()) if m == 'header_mismatch' else guid
        return _Resp(201, {'patient_guid': guid}, {'X-PDHC-Patient-Guid': header})


@pytest.fixture
def eps_register(monkeypatch):
    fake = FakeEpsRegister()
    monkeypatch.setattr('src.services.eps_client._http_post', fake)
    return fake
