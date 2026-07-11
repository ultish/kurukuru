"""Agent backends for stage execution."""

from board.backends.base import AgentBackend, StageProcessResult
from board.backends.claude import ClaudeBackend, ClaudeNotFoundError, find_claude
from board.backends.cmd import CmdBackend
from board.backends.grok import GrokBackend, GrokNotFoundError, find_grok
from board.backends.mock import MockBackend, load_mock_scenarios

__all__ = [
    "AgentBackend",
    "StageProcessResult",
    "MockBackend",
    "load_mock_scenarios",
    "ClaudeBackend",
    "ClaudeNotFoundError",
    "find_claude",
    "CmdBackend",
    "GrokBackend",
    "GrokNotFoundError",
    "find_grok",
]
