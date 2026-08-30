"""A bytes subclass that tolerates being concatenated with str, by
implicitly UTF-8-encoding it. This matches what CircuitPython does, and was
confirmed against real hardware. CPython raises TypeError instead.

This can't be done by monkeypatching bytes.__add__, since Python refuses to
patch methods on immutable builtin types. Instead, an AST transform
rewrites every bytes literal in app code into LenientBytes.wrap(...) at
compile time, before exec. Nothing outside that transformed code is
affected: regular bytes objects (socket data, this file's own literals,
and so on) are untouched and still satisfy isinstance(x, bytes) normally,
since this is a real subclass, not a replacement for the type itself.
"""


class LenientBytes(bytes):
    # Narrower than bytes.__add__'s real signature (any buffer-like type,
    # not just bytes | str) on purpose: this only ever wraps AST-rewritten
    # literals, which are always bytes or str, never an arbitrary buffer.
    def __add__(self, other: "bytes | str") -> "LenientBytes":  # ty: ignore[invalid-method-override]
        if isinstance(other, str):
            other = other.encode("utf-8")

        return LenientBytes(bytes(self) + other)

    def __radd__(self, other: "bytes | str") -> "LenientBytes":
        if isinstance(other, str):
            other = other.encode("utf-8")

        return LenientBytes(other + bytes(self))


def wrap(value: bytes) -> LenientBytes:
    return LenientBytes(value)
