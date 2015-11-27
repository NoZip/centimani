import re


class RoutingError(Exception):
    pass


class Router:
    @staticmethod
    def _build_route(route):
        pattern, handler_factory = route
        return (re.compile(pattern), handler_factory)

    def __init__(self, routes):
        self._routes = tuple(map(self._build_route, routes))

    def find_route(self, path):
        for pattern, handler_factory in self._routes:
            match = pattern.match(path)
            if match:
                return (handler_factory, match.groups(), match.groupdict())

        raise RoutingError
