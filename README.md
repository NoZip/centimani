# Centimani

A basic Python client and server library using **asyncio** package.

Prerequisites:
 - Python 3.5.1+
 - For SSL/TLS support openssl must been installed.

## Server

A simple HTTP server setup.

```python
import asyncio
from centimani.server import Server

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
```

In order to create an HTTPS server you must pass an `SSLContext` to the
server constructor.

```python
ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ssl_context.load_cert_chain(certfile="<name>.crt", keyfile="<name>.key")

server = Server(routes, loop=loop, ssl=ssl_context)
```

### Request Handler

Request handlers must be defined as subclasses of `RequestHandler`. Methods that matches HTTP methods (but lowercase) will be called when a request with this method is received.

```python
from centimani.headers import Headers
from centimani.server import RequestHandler

class EchoRequestHandler(RequestHandler):
    async def get(self):
      header_fields = Headers(content_type="text/plain")
      await self.send(200, header_fields, "Echo")
```

## Client

Simple HTTP request:

```python
import asyncio
from centimani.client import Client

loop = asyncio.get_event_loop()
client = Client()
task = client.fetch("<url>")
response = loop.run_until_complete(task)
loop.close()
```

Concurrent requests:

```python
import asyncio
from centimani.client import Client

loop = asyncio.get_event_loop()
client = Client()

urls = [
  # bunch of urls
]

tasks = [client.fetch(url) for url in urls]
for task in asyncio.as_completed(tasks):
    response = await task
    # Do stuff with the response

loop.close()
```
