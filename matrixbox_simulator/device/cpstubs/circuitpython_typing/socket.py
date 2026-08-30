"""Type-hint-only stand-in for the real `circuitpython-typing` PyPI package.
Vendored adafruit_requests/adafruit_connection_manager import these names
whenever sys.implementation.name != "circuitpython" (i.e. exactly our sim);
they're only ever used as annotations, so plain aliases are enough.
"""

from typing import Any

CircuitPythonSocketType = Any
InterfaceType = Any
SocketpoolModuleType = Any
SocketType = Any
SSLContextType = Any
