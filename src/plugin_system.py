"""Plugin system for chatbot extensibility."""

# Available hook constants
HOOK_PRE_CLASSIFY = "pre_classify"
HOOK_POST_CLASSIFY = "post_classify"
HOOK_PRE_MATCH = "pre_match"
HOOK_POST_MATCH = "post_match"
HOOK_PRE_RESPONSE = "pre_response"
HOOK_POST_RESPONSE = "post_response"


class PluginManager:
    """Manages plugin registration and execution via a hook-based pipeline."""

    def __init__(self):
        self._registry: dict[str, list[dict]] = {}

    def register(self, hook_name: str, plugin_fn: callable, priority: int = 10):
        """Register a plugin function for a specific hook.

        Lower priority number means the function runs first.
        """
        if hook_name not in self._registry:
            self._registry[hook_name] = []

        self._registry[hook_name].append({
            "fn": plugin_fn,
            "priority": priority,
        })

        # Keep sorted by priority so execute order is always correct
        self._registry[hook_name].sort(key=lambda entry: entry["priority"])

    def unregister(self, hook_name: str, plugin_fn: callable):
        """Remove a plugin function from a hook."""
        if hook_name not in self._registry:
            return

        self._registry[hook_name] = [
            entry for entry in self._registry[hook_name]
            if entry["fn"] is not plugin_fn
        ]

        if not self._registry[hook_name]:
            del self._registry[hook_name]

    def execute(self, hook_name: str, data: dict) -> dict:
        """Execute all registered plugins for a hook in priority order.

        Each plugin receives the data dict and must return a (possibly modified)
        data dict. This creates a pipeline where each plugin can transform the
        data before passing it to the next.
        """
        if hook_name not in self._registry:
            return data

        for entry in self._registry[hook_name]:
            result = entry["fn"](data)
            if isinstance(result, dict):
                data = result

        return data

    def list_plugins(self) -> dict:
        """Return a summary of all registered plugins.

        Returns:
            dict mapping hook_name to list of {"fn_name": str, "priority": int}.
        """
        summary = {}
        for hook_name, entries in self._registry.items():
            summary[hook_name] = [
                {
                    "fn_name": entry["fn"].__name__,
                    "priority": entry["priority"],
                }
                for entry in entries
            ]
        return summary
