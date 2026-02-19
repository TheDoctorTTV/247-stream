"""Minimal in-process signal shim for headless runtime.

This project is terminal-first and does not depend on desktop GUI frameworks.
"""

import threading
from typing import Callable, List


class _SignalInstance:
    def __init__(self):
        self._callbacks: List[Callable[..., None]] = []
        self._lock = threading.Lock()

    def connect(self, cb, *args, **kwargs):
        with self._lock:
            self._callbacks.append(cb)

    def emit(self, *args, **kwargs):
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(*args, **kwargs)
            except Exception:
                pass


class _SignalDescriptor:
    def __set_name__(self, owner, name):
        self._name = f"__signal_{name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        sig = instance.__dict__.get(self._name)
        if sig is None:
            sig = _SignalInstance()
            instance.__dict__[self._name] = sig
        return sig


class _QObjectShim:
    def __init__(self, *args, **kwargs):
        pass


class _QtCoreShim:
    QObject = _QObjectShim

    @staticmethod
    def Signal(*args, **kwargs):
        return _SignalDescriptor()

    @staticmethod
    def Slot(*args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator

    class Qt:
        class ConnectionType:
            DirectConnection = 0


QtCore = _QtCoreShim()  # type: ignore
