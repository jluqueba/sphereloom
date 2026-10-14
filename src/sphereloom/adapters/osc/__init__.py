"""The Wi-Fi backend: Open Spherical Camera (OSC) over HTTP.

Only this package may import `httpx` or know an OSC endpoint, which a codebase rule
enforces. Everything above it speaks in domain models and taxonomy errors.
"""
