"""Sizes shared by the agent and the backend.

This module imports nothing, so the backend's settings can read a size without loading the engine
or the transport.
"""

# The worker threads that run MCP requests. Confirmation waits are capped below this size.
THREAD_POOL_SIZE = 40
