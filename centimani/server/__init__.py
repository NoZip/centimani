"""This module defines classes and function that enables to set up an
HTTP Server.

Simple HTTP server set up:
    
    loop = asyncio.get_event_loop()

    routes = [
        (r"/", RootRequestHandler),
        (r"/user", UserRequestHandler),
        ...
    ]

    server = Server(routes, loop=loop)
    loop.run_until_complete(server.listen(port=8080))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass

    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()

In order to create an HTTPS server you must pass an ``SSLContext`` to the
server constructor.

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(certfile="<name>.crt", keyfile="<name>.key")

    server = Server(routes, loop=loop, ssl=ssl_context)
"""

from .manager import Server
from .handlers import RequestHandler
