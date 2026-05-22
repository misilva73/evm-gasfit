"""evm-gasfit: estimate worst-case EVM gas costs from runtime measurements.

Public re-exports land here once the corresponding submodules are implemented.
Until then, importing unimplemented symbols (e.g. `from evm_gasfit import
GasFit`) will raise ImportError, which the e2e test suite uses to drive the
implementation.
"""

__version__ = "0.0.1"
