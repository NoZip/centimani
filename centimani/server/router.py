import re


class RoutingError(Exception):
    pass


class Router:
    """This function build a routing structure, and find request handlers
    associated to a specific path.
    """
    @staticmethod
    def _build_route(route):
        pattern, handler_factory = route
        return (re.compile(pattern), handler_factory)

    def __init__(self, routes):
        self._routes = tuple(map(self._build_route, routes))

    def find_route(self, path):
        """Find the route associated to ``path``. Raises a ``RoutingError``
        if not found.
        """
        for pattern, handler_factory in self._routes:
            match = pattern.match(path)
            if match:
                return (handler_factory, match.groups(), match.groupdict())

        raise RoutingError
