"""Always-on wake-word listener for the laptop.

The Raspberry Pi client in `api/client` records whatever it hears and ships all
of it to the server. That is fine for a dedicated box in a corner; it is not
fine for a laptop that sits in front of you all day. This package listens
locally for a wake phrase and only then opens the microphone for real.
"""
