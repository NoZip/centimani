import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    datefmt="%Y-%m-%dT%H:%M:%S",
    format="#%(levelname)s %(name)s %(asctime)s,%(msecs)03d\n%(message)s"
)
