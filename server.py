"""Separate opt-in browser HTTP and native pinned-TLS LAN listeners."""
import threading,json,os
from cheroot.wsgi import Server
from cheroot.ssl.builtin import BuiltinSSLAdapter
from runtime import ROOT,CONFIG,DATA_DIR,WEB_PORT,LAN_PORT,LAN_ENABLED,WEB_LAN_ENABLED


def run():
    from app import create_app,local_addresses,LEGACY
    from processing import start_worker
    from transport import certificate
    application=create_app();servers=[]
    servers.append(Server(('127.0.0.1',WEB_PORT),application,numthreads=8))
    if WEB_LAN_ENABLED:
        for address in local_addresses():
            servers.append(Server((address,WEB_PORT),application,numthreads=8))
    if LAN_ENABLED:
        cert,key,_=certificate()
        for address in local_addresses():
            server=Server((address,LAN_PORT),application,numthreads=8)
            server.ssl_adapter=BuiltinSSLAdapter(str(cert),str(key))
            servers.append(server)
    start_worker(DATA_DIR,LEGACY)
    work=ROOT/'work';work.mkdir(exist_ok=True)
    pidfile=work/'server.pid.json'
    pidfile.write_text(json.dumps({'pid':os.getpid(),'instance':CONFIG.get('install_id','')}),encoding='utf-8')
    print(f'记忆导演本机入口： http://localhost:{WEB_PORT}',flush=True)
    for server in servers[1:]:threading.Thread(target=server.start,daemon=True).start()
    try:servers[0].start()
    except KeyboardInterrupt:pass
    finally:
        for server in servers:server.stop()
        pidfile.unlink(missing_ok=True)

if __name__=='__main__':run()
