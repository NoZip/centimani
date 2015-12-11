class ClientError(Exception):
    pass

class ClientConnectionError(ClientError):
    pass

class ClientTimeoutError(ClientConnectionError, TimeoutError):
    pass
