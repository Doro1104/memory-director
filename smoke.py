"""Self-test with synthetic images and isolated accounts. Never opens a real library."""
import io,json,ssl,socket,threading,uuid
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from cheroot.wsgi import Server
from cheroot.ssl.builtin import BuiltinSSLAdapter

from runtime import ROOT
from app import create_app
from transport import certificate
import transport


def run():
    folder=ROOT/'work'/('smoke-'+uuid.uuid4().hex)
    application=create_app(folder);client=application.test_client()
    # Network use by normal request handling is restricted to loopback in this test.
    original_connect=socket.socket.connect
    def local_only(sock,address):
        if isinstance(address,tuple) and address[0] not in ('localhost','127.0.0.1','::1'):
            raise AssertionError('Unexpected outbound connection')
        return original_connect(sock,address)
    with patch.object(socket.socket,'connect',local_only):
        assert client.post('/api/setup',json={'name':'test-user','password':uuid.uuid4().hex}).status_code==200
        csrf=client.get('/api/session').json['csrf']
        photo=io.BytesIO();Image.new('RGB',(1800,1200),'#70856b').save(photo,format='PNG')
        upload=client.post('/api/upload?name=synthetic.png',data=photo.getvalue(),headers={'X-CSRF-Token':csrf})
        assert upload.status_code==200
        aid=upload.json['id'];assert client.get('/media/'+aid+'/display').data==photo.getvalue()
        other=application.test_client();assert other.get('/media/'+aid+'/display').status_code==401
        assert client.get('/api/assets').json['total']==1
        from processing import connection
        from curation import analyze_asset
        from film_rules import text_vector
        conn=connection(folder)
        value=analyze_asset(conn,conn.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone())
        assert len(value['embedding'])==512 and len(text_vector('模拟风景照片'))==512
        conn.close()
        # The TLS identity and private key are generated in the disposable test folder.
        with patch.object(transport,'DATA_DIR',folder):
            cert,key,fingerprint=certificate()
            assert certificate()[2]==fingerprint
            server=Server(('127.0.0.1',0),application,numthreads=2)
            server.ssl_adapter=BuiltinSSLAdapter(str(cert),str(key))
            server.prepare();thread=threading.Thread(target=server.serve,daemon=True);thread.start()
            try:
                context=ssl.create_default_context(cafile=str(cert))
                with context.wrap_socket(socket.socket(),server_hostname='localhost') as channel:
                    channel.connect(server.bind_addr)
                    import hashlib
                    assert hashlib.sha256(channel.getpeercert(binary_form=True)).hexdigest()==fingerprint
                    channel.sendall(b'GET /api/health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                    assert b'200 OK' in channel.recv(4096)
            finally:server.stop()
    return {'synthetic_upload':'ok','full_size_view':'ok','account_boundary':'ok','local_semantic_and_expression_models':'ok','pinned_tls_handshake':'ok','runtime_external_connection':'none','family_media_read':False}

if __name__=='__main__':print(json.dumps(run(),indent=2))
