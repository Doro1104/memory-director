import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import app as module


@pytest.fixture
def web(tmp_path):
    return module.create_app(tmp_path/'data').test_client()


def test_public_install_assets_and_network_probe_need_no_account(web):
    manifest=web.get('/static/manifest.webmanifest')
    assert manifest.status_code==200
    value=json.loads(manifest.data)
    assert value['display']=='standalone' and value['start_url']=='/' and value['scope']=='/'
    for size in (180,192,512):
        with Image.open(io.BytesIO(web.get(f'/static/icons/app-{size}.png').data)) as icon:
            assert icon.size==(size,size) and not icon.getexif()
    assert web.get('/api/reachability').json=={'ok':True}
    assert web.get('/api/reachability').headers['Cache-Control']=='no-store'
    assert web.get('/api/session').json['setup'] is True
    assert web.get('/api/assets').status_code==401
    assert web.get('/api/connect').status_code==401
    assert web.get('/api/qr?mode=web').status_code==401
    assert web.get('/api/reachability',base_url='http://evil.example').status_code==403


def test_worker_scope_and_private_responses_are_not_browser_cached(web):
    worker=web.get('/sw.js')
    assert worker.status_code==200 and worker.mimetype=='text/javascript'
    assert worker.headers['Cache-Control']=='no-cache'
    assert web.post('/api/setup',json={'name':'synthetic','password':'synthetic-password'}).status_code==200
    for url in ('/','/api/session','/api/assets','/api/connect','/media/not-real/thumb'):
        assert web.get(url).headers['Cache-Control']=='no-store'


@pytest.mark.skipif(importlib.util.find_spec('transport') is None,reason='Public distribution transport only')
def test_web_qr_is_url_and_native_pairing_is_separate(web,tmp_path,monkeypatch):
    import cv2
    import numpy as np
    import transport
    monkeypatch.setattr(transport,'DATA_DIR',tmp_path)
    monkeypatch.setattr(transport,'WEB_LAN_ENABLED',True)
    monkeypatch.setattr(transport,'LAN_ENABLED',True)
    monkeypatch.setattr(module,'local_addresses',lambda:['192.168.50.8'])
    web.post('/api/setup',json={'name':'synthetic','password':'synthetic-password'})
    info=web.get('/api/connect').json
    assert info['web_urls']==[f'http://192.168.50.8:{transport.WEB_PORT}']
    assert info['pairing'][0]['url'].startswith('https:')
    def decode(url):
        response=web.get(url);assert response.status_code==200
        pixels=cv2.imdecode(np.frombuffer(response.data,np.uint8),cv2.IMREAD_GRAYSCALE)
        return cv2.QRCodeDetector().detectAndDecode(pixels)[0]
    assert decode('/api/qr?mode=web&index=0')==info['web_urls'][0]
    assert json.loads(decode('/api/qr?index=0'))==info['pairing'][0]
    for query in ('mode=unknown','mode=web&index=-1','mode=web&index=8','index=no'):
        assert web.get('/api/qr?'+query).status_code==400
    monkeypatch.setattr(transport,'WEB_LAN_ENABLED',False)
    assert web.get('/api/connect').json['web_urls']==[]
    assert web.get('/api/qr?mode=web').status_code==400


@pytest.mark.skipif(importlib.util.find_spec('transport') is None,reason='Public distribution transport only')
@pytest.mark.parametrize('web_lan,native_lan',[(False,False),(True,False),(False,True),(True,True)])
def test_server_lan_listeners_require_their_own_opt_in(tmp_path,monkeypatch,web_lan,native_lan):
    import server
    import transport
    import processing
    monkeypatch.setattr(server,'ROOT',tmp_path)
    monkeypatch.setattr(server,'WEB_LAN_ENABLED',web_lan)
    monkeypatch.setattr(server,'LAN_ENABLED',native_lan)
    monkeypatch.setattr(module,'create_app',lambda:object())
    monkeypatch.setattr(module,'local_addresses',lambda:['192.168.50.8'])
    monkeypatch.setattr(processing,'start_worker',lambda *args:None)
    monkeypatch.setattr(transport,'certificate',lambda:('synthetic.crt','synthetic.key','public-fingerprint'))
    constructor=MagicMock();constructor.return_value.start.side_effect=KeyboardInterrupt
    with patch.object(server,'Server',constructor),patch.object(server,'BuiltinSSLAdapter'),patch.object(server.threading,'Thread'):
        server.run()
    bindings=[call.args[0] for call in constructor.call_args_list]
    expected=[('127.0.0.1',server.WEB_PORT)]
    if web_lan:expected.append(('192.168.50.8',server.WEB_PORT))
    if native_lan:expected.append(('192.168.50.8',server.LAN_PORT))
    assert bindings==expected
