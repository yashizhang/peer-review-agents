"""
reva view — interactive Textual TUI for watching agent activity.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    Markdown,
    RichLog,
    Select,
    TabbedContent,
    TabPane,
)

from reva.tmux import list_sessions


# --------------------------------------------------------------------------- #
# helpers (adapted from cli.py)
# --------------------------------------------------------------------------- #


def _summarize_tool_input(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return inp.get("command", "").strip()
    if tool == "WebFetch":
        return inp.get("url", "")
    if tool in ("Write", "Edit"):
        return inp.get("file_path", "")
    if tool == "Read":
        return inp.get("file_path", "")
    if tool == "Skill":
        return inp.get("skill", "")
    if tool in ("Grep", "Glob"):
        return inp.get("pattern", "") or inp.get("query", "")
    return json.dumps(inp, ensure_ascii=False)[:200]


# Tool name → color
_TOOL_COLORS: dict[str, str] = {
    "Bash": "dark_orange",
    "Read": "steel_blue",
    "Write": "medium_orchid",
    "Edit": "medium_orchid",
    "WebFetch": "turquoise2",
    "WebSearch": "turquoise2",
    "Grep": "khaki1",
    "Glob": "khaki1",
    "Skill": "spring_green2",
}


def _parse_log_line(line: str) -> list[Text]:
    """Parse one stream-json line into Rich Text objects for RichLog."""
    if not line:
        return []
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        # plain-text log line (e.g. gemini-cli output)
        t = Text()
        if line.startswith("[reva]"):
            t.append(line, style="bold dim")
        else:
            t.append(line, style="bright_white")
        return [t]

    if not isinstance(d, dict):
        t = Text()
        rendered = json.dumps(d, ensure_ascii=False)
        t.append(rendered[:1000], style="color(245)")
        return [t]

    typ = d.get("type")
    out: list[Text] = []

    if typ == "system" and d.get("subtype") == "init":
        model = d.get("model", "?")
        t = Text()
        t.append("\n▶ session started  ", style="bold bright_green")
        t.append(f"model={model}", style="green")
        out.append(t)

    elif typ == "assistant":
        for block in d.get("message", {}).get("content", []):
            btype = block.get("type")

            if btype == "thinking":
                thought = block.get("thinking", "").strip()
                if thought:
                    t = Text()
                    t.append("\n💭 thinking\n", style="bold color(244)")
                    t.append(f"   {thought}", style="italic color(240)")
                    out.append(t)

            elif btype == "text":
                text = block.get("text", "").strip()
                if text:
                    t = Text()
                    t.append("\n» ", style="bold bright_cyan")
                    t.append(text, style="bright_white")
                    out.append(t)

            elif btype == "tool_use":
                tool = block.get("name", "?")
                inp = block.get("input", {})
                summary = _summarize_tool_input(tool, inp)
                color = _TOOL_COLORS.get(tool, "yellow")
                t = Text()
                t.append(f"\n⚙ {tool}", style=f"bold {color}")
                if summary:
                    t.append(f"\n  {summary}", style=f"dim {color}")
                out.append(t)

    elif typ == "user":
        for block in d.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                result = block.get("content", "")
                if isinstance(result, list):
                    result = " ".join(r.get("text", "") for r in result if isinstance(r, dict))
                if result and result.strip():
                    t = Text()
                    t.append("  ← ", style="dim")
                    t.append(result.strip()[:400], style="color(245)")
                    out.append(t)

    elif typ == "result":
        cost = d.get("cost_usd")
        turns = d.get("num_turns")
        cost_str = f"  cost=${cost:.4f}" if cost else ""
        t = Text()
        t.append(f"\n■ session ended  turns={turns}{cost_str}\n", style="bold red")
        out.append(t)

    elif typ == "rate_limit_event":
        status = d.get("rate_limit_info", {}).get("status", "?")
        if status != "allowed":
            t = Text()
            t.append(f"⚠ rate limit: {status}", style="bold magenta")
            out.append(t)

    return out


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #


class RevaViewer(App):
    TITLE = "reva viewer"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_agents", "Refresh"),
    ]
    CSS = """
    #toolbar {
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary;
    }
    #agent-select {
        width: 1fr;
    }
    #refresh-btn {
        width: 12;
        margin-left: 1;
    }
    #output-log {
        scrollbar-gutter: stable;
    }
    #prompt-scroll {
        padding: 1 2;
    }
    #agent-table {
        height: 1fr;
    }
    TabbedContent {
        height: 1fr;
    }
    """

    def __init__(self, cfg, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self._current_agent: str | None = None
        self._tail_running = False
        self._known_agents: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Label("Agent: ", classes="label")
            yield Select([], id="agent-select", prompt="— pick an agent —")
            yield Button("Refresh", id="refresh-btn", variant="primary")
        with TabbedContent():
            with TabPane("Output", id="tab-output"):
                yield RichLog(id="output-log", highlight=False, markup=False, wrap=True)
            with TabPane("System Prompt", id="tab-prompt"):
                with VerticalScroll(id="prompt-scroll"):
                    yield Markdown("", id="system-prompt")
            with TabPane("Agent Info", id="tab-info"):
                yield DataTable(id="agent-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#agent-table", DataTable)
        table.add_columns("Field", "Value")
        self._populate_agent_list()
        self.set_interval(5, self._populate_agent_list)

    # ------------------------------------------------------------------ #
    # agent list
    # ------------------------------------------------------------------ #

    def _get_agent_names(self) -> list[str]:
        """All agents with a config.json (running or not)."""
        running = {s.agent_name for s in list_sessions()}
        names = set()
        if self.cfg.agents_dir.exists():
            for d in self.cfg.agents_dir.iterdir():
                if d.is_dir() and (d / "config.json").exists():
                    names.add(d.name)
        return sorted(running, key=str) + sorted(names - running, key=str)

    def _populate_agent_list(self) -> None:
        names = self._get_agent_names()
        if names == self._known_agents:
            return
        self._known_agents = names
        sel = self.query_one("#agent-select", Select)
        sel.set_options([(name, name) for name in names])
        if self._current_agent and self._current_agent in names:
            sel.value = self._current_agent

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        name = str(event.value)
        if name == self._current_agent:
            return
        self._current_agent = name
        self._load_agent(name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            self._populate_agent_list()

    def action_refresh_agents(self) -> None:
        self._populate_agent_list()

    # ------------------------------------------------------------------ #
    # agent loading
    # ------------------------------------------------------------------ #

    def _load_agent(self, name: str) -> None:
        agent_dir = self.cfg.agents_dir / name

        # output log
        log_widget = self.query_one("#output-log", RichLog)
        log_widget.clear()
        self._tail_running = False
        time.sleep(0.05)
        log_path = agent_dir / "agent.log"
        if log_path.exists():
            self._tail_log(log_path)
        else:
            log_widget.write(Text("(no log yet — agent not yet launched)", style="dim"))

        # Read config.json once — used for both the prompt file selection
        # and the agent info table below.
        config_path = agent_dir / "config.json"
        cfg_data: dict | None = None
        if config_path.exists():
            try:
                cfg_data = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg_data = None

        # system prompt — pick the right file for this backend
        prompt_widget = self.query_one("#system-prompt", Markdown)
        prompt_file = agent_dir / "prompt.md"  # fallback
        if cfg_data is not None:
            try:
                from reva.backends import get_backend
                backend_file = get_backend(cfg_data["backend"]).prompt_filename
                candidate = agent_dir / backend_file
                if candidate.exists():
                    prompt_file = candidate
            except Exception:
                pass
        if prompt_file.exists():
            self.call_later(prompt_widget.update, prompt_file.read_text(encoding="utf-8"))
        else:
            self.call_later(prompt_widget.update, "_No system prompt found._")

        # agent info
        table = self.query_one("#agent-table", DataTable)
        table.clear()
        if cfg_data is not None:
            for key, val in cfg_data.items():
                if isinstance(val, str) and "/" in val:
                    val = Path(val).name
                table.add_row(key, str(val))
        running = {s.agent_name for s in list_sessions()}
        table.add_row("status", "running" if name in running else "stopped")

    # ------------------------------------------------------------------ #
    # log tailing
    # ------------------------------------------------------------------ #

    @work(thread=True)
    def _tail_log(self, log_path: Path) -> None:
        self._tail_running = True
        log_widget = self.query_one("#output-log", RichLog)
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            while self._tail_running:
                line = fh.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                line = line.strip()
                if not line:
                    continue
                for text_obj in _parse_log_line(line):
                    self.call_from_thread(log_widget.write, text_obj)
