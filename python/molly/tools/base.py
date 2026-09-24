from abc import ABC, abstractmethod


class ToolError(Exception):
    """Raised when a tool encounters an expected failure condition."""

    pass


class ToolBase(ABC):
    """Base class for all armory tools."""

    capability_name: str

    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the tool."""

    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""

    @abstractmethod
    def parameters_schema(self) -> dict:
        """OpenAI-compatible JSON schema for tool parameters."""

    @abstractmethod
    async def execute(self, **kwargs) -> dict:
        """Execute the tool with the given parameters."""
