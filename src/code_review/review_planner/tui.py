from __future__ import annotations

import curses
import sys
from dataclasses import dataclass

from .catalog import DEFAULT_BASELINES, DEFAULT_PERSONAS, DEFAULT_TOOLS, Pack, Persona, Strategy, ToolPack
from .planner import expand_specialty_hierarchy, infer_tool_ids

TOOL_APPROVAL_NOTE = "Allowing a tool also authorizes crk to install it when it is missing or not accessible."


@dataclass
class Option:
    id: str
    title: str
    selected: bool
    group: str | None = None
    is_group_header: bool = False
    label: str | None = None


@dataclass
class Page:
    key: str
    title: str
    subtitle: str
    options: list[Option]
    required: bool = False
    single_select: bool = False


def _table_lines(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not headers:
        return []
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))
    header_line = "| " + " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)) + " |"
    separator = "|-" + "-|-".join("-" * widths[index] for index in range(len(headers))) + "-|"
    lines = [header_line, separator]
    for row in rows:
        cells = [row[index] if index < len(row) else "" for index in range(len(headers))]
        lines.append("| " + " | ".join(cells[index].ljust(widths[index]) for index in range(len(headers))) + " |")
    return lines


def render_setup_summary_lines(
    *, plan: dict, tool_setup_results: list[dict], tool_setup_error: str | None
) -> list[str]:
    selections = plan.get("selections", {})
    setup_tool_policy = plan.get("setup_tool_policy", {})
    approval_mode = str(setup_tool_policy.get("mode", "prompt")) if isinstance(setup_tool_policy, dict) else "prompt"
    setup_rows = [
        ["Target", str(plan.get("target", ""))],
        ["Personas", ", ".join(selections.get("personas", [])) or "none"],
        ["Baselines", ", ".join(selections.get("baselines", [])) or "none"],
        ["Tools", ", ".join(selections.get("tools", [])) or "none"],
        ["Languages", ", ".join(selections.get("languages", [])) or "none"],
        ["Specialties", ", ".join(selections.get("specialties", [])) or "none"],
        ["Strategies", ", ".join(selections.get("strategies", [])) or "none"],
        ["Setup tool approval", approval_mode],
    ]
    lines = [
        "Code review setup",
        "",
        *_table_lines(["Setting", "Value"], setup_rows),
        "",
        "Tool setup results",
        "",
    ]
    if tool_setup_error:
        lines.append(f"Error: {tool_setup_error}")
        lines.append("")

    tool_rows: list[list[str]] = []
    for result in tool_setup_results:
        steps = result.get("steps", [])
        prereq = "; ".join(
            step.get("text", "")
            for step in steps
            if isinstance(step, dict) and step.get("kind") in {"prereq", "setup"} and step.get("text")
        )
        verify = "; ".join(
            step.get("text", "")
            for step in steps
            if isinstance(step, dict) and step.get("kind") == "verify" and step.get("text")
        )
        tool_rows.append([str(result.get("id", "")), str(result.get("status", "")), prereq or "-", verify or "-"])

    if tool_rows:
        lines.extend(_table_lines(["Tool", "Status", "Prereq/setup", "Verify"], tool_rows))
    else:
        lines.append("No selected tool packs required setup.")
    lines.extend(["", "Press Enter to exit."])
    lines[-1] = "✅ Setup complete. Press Enter to exit."
    return lines


def show_setup_summary(*, plan: dict, tool_setup_results: list[dict], tool_setup_error: str | None) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    curses.wrapper(_run_setup_summary, plan, tool_setup_results, tool_setup_error)


def _run_setup_summary(
    stdscr: curses.window,
    plan: dict,
    tool_setup_results: list[dict],
    tool_setup_error: str | None,
) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.noecho()
    curses.cbreak()
    try:
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            lines = render_setup_summary_lines(
                plan=plan,
                tool_setup_results=tool_setup_results,
                tool_setup_error=tool_setup_error,
            )
            for row, line in enumerate(lines[: height - 1]):
                stdscr.addnstr(row, 2, line, width - 4, curses.A_BOLD if row == 0 else curses.A_NORMAL)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return
    finally:
        curses.nocbreak()
        stdscr.keypad(False)
        curses.echo()
        curses.curs_set(1)


class ReviewWizard:
    def __init__(
        self,
        *,
        target: str,
        personas: dict[str, Persona],
        baselines: dict[str, Pack],
        tools: dict[str, ToolPack],
        languages: dict[str, Pack],
        specialties: dict[str, Pack],
        strategies: dict[str, Strategy],
        config: dict,
    ) -> None:
        self.target = target
        self.status_message = ""
        self.tools = tools
        self.baselines_catalog = baselines
        self.languages_catalog = languages
        self.specialties_catalog = specialties
        self.initial_tool_selected = self._selected_ids(config, "tools", DEFAULT_TOOLS)
        self.initial_specialty_selected = self._selected_ids(config, "specialties", [])
        self.initial_baseline_practice_selected = self._initial_practice_selection(
            config=config,
            pack_kind="baseline",
            catalog=baselines,
            config_pack_key="baselines",
            config_practice_key="baseline_practices",
            default_pack_ids=DEFAULT_BASELINES,
        )
        self.initial_language_practice_selected = self._initial_practice_selection(
            config=config,
            pack_kind="language",
            catalog=languages,
            config_pack_key="languages",
            config_practice_key="language_practices",
            default_pack_ids=[],
        )
        self.tools_user_modified = False
        self.baseline_practices_user_modified = False
        self.language_practices_user_modified = False
        self.show_all_tools = False
        self.pages = self._build_pages(personas, baselines, tools, languages, specialties, strategies, config)
        self.page_index = 0
        self.cursor_index = 0
        self.scroll_offset = 0

    def _selected_ids(self, config: dict, key: str, default: list[str]) -> set[str]:
        raw = config.get(key, default)
        if not isinstance(raw, list):
            return set(default)
        return {item for item in raw if isinstance(item, str)}

    def _practice_id(self, *, pack_kind: str, pack_id: str, index: int) -> str:
        return f"{pack_kind}::{pack_id}::{index}"

    def _group_id(self, *, pack_kind: str, pack_id: str) -> str:
        return f"group::{pack_kind}::{pack_id}"

    def _parse_practice_id(self, value: str) -> tuple[str, str, int] | None:
        parts = value.split("::", 2)
        if len(parts) != 3:
            return None
        kind, pack_id, raw_index = parts
        if not raw_index.isdigit():
            return None
        return kind, pack_id, int(raw_index)

    def _parse_group_id(self, value: str) -> tuple[str, str] | None:
        parts = value.split("::", 2)
        if len(parts) != 3:
            return None
        root, kind, pack_id = parts
        if root != "group":
            return None
        return kind, pack_id

    def _initial_practice_selection(
        self,
        *,
        config: dict,
        pack_kind: str,
        catalog: dict[str, Pack],
        config_pack_key: str,
        config_practice_key: str,
        default_pack_ids: list[str],
    ) -> set[str]:
        direct = self._selected_ids(config, config_practice_key, [])
        if direct:
            return direct
        selected_pack_ids = self._selected_ids(config, config_pack_key, default_pack_ids)
        selected: set[str] = set()
        for pack_id in selected_pack_ids:
            pack = catalog.get(pack_id)
            if pack is None:
                continue
            for index, _practice in enumerate(pack.practices):
                selected.add(self._practice_id(pack_kind=pack_kind, pack_id=pack_id, index=index))
        return selected

    def _build_pages(
        self,
        personas: dict[str, Persona],
        baselines: dict[str, Pack],
        tools: dict[str, ToolPack],
        languages: dict[str, Pack],
        specialties: dict[str, Pack],
        strategies: dict[str, Strategy],
        config: dict,
    ) -> list[Page]:
        persona_selected = self._selected_ids(config, "personas", DEFAULT_PERSONAS)
        strategy_selected = self._selected_ids(config, "strategies", [])

        pages = [
            Page(
                key="personas",
                title="Choose reviewer personas",
                subtitle="Pick the core review lenses to include in the panel.",
                required=True,
                options=[
                    Option(option_id, persona.title, option_id in persona_selected)
                    for option_id, persona in personas.items()
                ],
            ),
            Page(
                key="baseline_practices",
                title="Choose baseline best practices",
                subtitle="Select baseline best practices individually (grouped by methodology pack).",
                options=[],
            ),
            Page(
                key="language_practices",
                title="Choose language best practices",
                subtitle="Select language-specific practices individually (grouped by language).",
                options=[],
            ),
            Page(
                key="tools",
                title="Allow tool execution and installation",
                subtitle=f"Select tools to allow. {TOOL_APPROVAL_NOTE}",
                options=[],
            ),
            Page(
                key="specialties",
                title="Choose specialty packs",
                subtitle="Choose core and overlay specialty packs grouped by hierarchy.",
                options=[],
            ),
            Page(
                key="strategies",
                title="Choose challenge strategies",
                subtitle="Select adversarial, devil's-advocate, or failure-mode overlays.",
                options=[
                    Option(option_id, strategy.title, option_id in strategy_selected)
                    for option_id, strategy in strategies.items()
                ],
            ),
            Page(
                key="harness",
                title="Choose AI harness",
                subtitle="Select the AI agent that will execute the persona reviews (single-select).",
                single_select=True,
                options=self._build_harness_options(config),
            ),
            Page(
                key="summary",
                title="Review summary",
                subtitle="Press S or Enter to start the code review.",
                options=[],
            ),
        ]
        return pages

    def _build_harness_options(self, config: dict) -> list[Option]:
        harness_choices = [
            ("copilot", "GitHub Copilot"),
            ("claude-code", "Claude Code"),
            ("opencode", "OpenCode"),
        ]
        current = config.get("harness", "")
        selected_id = current if isinstance(current, str) and current else ""
        if not selected_id and harness_choices:
            selected_id = harness_choices[0][0]
        return [Option(h_id, label, h_id == selected_id) for h_id, label in harness_choices]

    def _page_for_key(self, key: str) -> Page:
        for page in self.pages:
            if page.key == key:
                return page
        raise ValueError(f"Missing page: {key}")

    def _selected_ids_for_page(self, key: str) -> list[str]:
        page = self._page_for_key(key)
        return [option.id for option in page.options if option.selected]

    def _is_grouped_practice_page(self, key: str) -> bool:
        return key in {"baseline_practices", "language_practices"}

    def _is_specialty_grouped_page(self, key: str) -> bool:
        return key == "specialties"

    def _practice_option_ids_for_page(self, page: Page) -> list[str]:
        ids: list[str] = []
        for option in page.options:
            if option.is_group_header:
                continue
            if self._parse_practice_id(option.id):
                ids.append(option.id)
        return ids

    def _group_rows(self, page: Page) -> dict[str, dict[str, object]]:
        groups: dict[str, dict[str, object]] = {}
        for index, option in enumerate(page.options):
            if option.is_group_header:
                parsed = self._parse_group_id(option.id)
                if parsed is None:
                    continue
                _kind, pack_id = parsed
                groups.setdefault(pack_id, {"header_index": index, "item_indexes": []})
            elif option.group:
                groups.setdefault(option.group, {"header_index": None, "item_indexes": []})
                groups[option.group]["item_indexes"].append(index)
        return groups

    def _selection_progress(self, page: Page) -> tuple[int, int]:
        selectable = [option for option in page.options if not option.is_group_header]
        selected = sum(1 for option in selectable if option.selected)
        return selected, len(selectable)

    def _refresh_group_headers(self, page: Page) -> None:
        if not self._is_grouped_practice_page(page.key):
            return
        groups = self._group_rows(page)
        for pack_id, group_data in groups.items():
            header_index = group_data.get("header_index")
            item_indexes = group_data.get("item_indexes", [])
            if header_index is None or not isinstance(header_index, int):
                continue
            header = page.options[header_index]
            selected_count = 0
            for item_index in item_indexes:
                if not isinstance(item_index, int):
                    continue
                if page.options[item_index].selected:
                    selected_count += 1
            total = len(item_indexes)
            header.selected = total > 0 and selected_count == total
            label = header.label or pack_id
            header.title = f"{label} [{selected_count}/{total}]"

    def _sync_specialty_page(self, specialties: dict[str, Pack]) -> None:
        page = self._page_for_key("specialties")
        selected_ids = [option.id for option in page.options if option.selected]
        if not selected_ids:
            selected_ids = list(self.initial_specialty_selected)
        selected_set = set(expand_specialty_hierarchy(selected_ids))

        roots: list[tuple[str, Pack]] = []
        children_by_parent: dict[str, list[tuple[str, Pack]]] = {}
        for specialty_id, pack in specialties.items():
            parent_id = pack.parent
            if parent_id and parent_id in specialties:
                children_by_parent.setdefault(parent_id, []).append((specialty_id, pack))
            else:
                roots.append((specialty_id, pack))

        options: list[Option] = []
        for specialty_id, pack in roots:
            options.append(Option(specialty_id, pack.title, specialty_id in selected_set))
            for child_id, child_pack in children_by_parent.get(specialty_id, []):
                options.append(Option(child_id, f"  {child_pack.title}", child_id in selected_set, group=specialty_id))

        page.options = options
        page.subtitle = "Choose core packs and child overlays with the same shared hierarchy pattern."
        self.cursor_index = min(self.cursor_index, max(0, len(page.options) - 1))

    def _move_cursor_to_group_header(self, *, direction: int) -> None:
        page = self._current_page()
        if not self._is_grouped_practice_page(page.key):
            return
        headers = [index for index, option in enumerate(page.options) if option.is_group_header]
        if not headers:
            return

        if direction > 0:
            target = next((index for index in headers if index > self.cursor_index), headers[0])
        else:
            reverse = [index for index in headers if index < self.cursor_index]
            target = reverse[-1] if reverse else headers[-1]
        self.cursor_index = target

    def _selected_pack_ids_from_practice_page(self, *, page_key: str, expected_kind: str) -> list[str]:
        page = self._page_for_key(page_key)
        if not page.options:
            seed = (
                self.initial_baseline_practice_selected
                if page_key == "baseline_practices"
                else self.initial_language_practice_selected
            )
            pack_ids: list[str] = []
            for option_id in seed:
                parsed = self._parse_practice_id(option_id)
                if parsed is None:
                    continue
                kind, pack_id, _index = parsed
                if kind == expected_kind and pack_id not in pack_ids:
                    pack_ids.append(pack_id)
            return pack_ids

        pack_ids: list[str] = []
        for option_id in self._selected_ids_for_page(page_key):
            parsed = self._parse_practice_id(option_id)
            if parsed is None:
                continue
            kind, pack_id, _index = parsed
            if kind == expected_kind and pack_id not in pack_ids:
                pack_ids.append(pack_id)
        return pack_ids

    def _sync_tools_page(self) -> None:
        page = self._page_for_key("tools")
        previous_selected = {option.id for option in page.options if option.selected}
        if not page.options and self.initial_tool_selected:
            previous_selected = set(self.initial_tool_selected)

        selected_baselines = self._selected_pack_ids_from_practice_page(
            page_key="baseline_practices",
            expected_kind="baseline",
        )
        selected_languages = self._selected_pack_ids_from_practice_page(
            page_key="language_practices",
            expected_kind="language",
        )
        selected_specialties = self._selected_ids_for_page("specialties")
        recommended = infer_tool_ids(
            selected_baselines=selected_baselines,
            selected_languages=selected_languages,
            selected_specialties=selected_specialties,
            tools=self.tools,
        )
        recommended_set = set(recommended)

        if self.show_all_tools or not recommended:
            option_ids = list(self.tools.keys())
        else:
            option_ids = list(recommended)
            for tool_id in previous_selected:
                if tool_id in self.tools and tool_id not in option_ids:
                    option_ids.append(tool_id)

        if not previous_selected and not self.tools_user_modified and not self.initial_tool_selected:
            selected_ids = recommended_set
        else:
            selected_ids = previous_selected

        page.options = [Option(tool_id, self.tools[tool_id].title, tool_id in selected_ids) for tool_id in option_ids]
        mode = "showing all tools" if self.show_all_tools else "showing recommended tools"
        if recommended:
            page.subtitle = (
                f"Language-informed tool menu ({mode}); press T to toggle all/recommended. {TOOL_APPROVAL_NOTE}"
            )
        else:
            page.subtitle = (
                f"No language-specific recommendation yet; showing all tools. Press T to toggle. {TOOL_APPROVAL_NOTE}"
            )
        self.cursor_index = min(self.cursor_index, max(0, len(page.options) - 1))

    def _sync_practice_page(
        self,
        *,
        page_key: str,
        pack_kind: str,
        catalog: dict[str, Pack],
        initial_selected: set[str],
        user_modified: bool,
    ) -> None:
        page = self._page_for_key(page_key)
        previous_selected = {
            option.id for option in page.options if option.selected and self._parse_practice_id(option.id)
        }
        if not page.options and initial_selected:
            previous_selected = {value for value in initial_selected if self._parse_practice_id(value)}

        option_rows: list[Option] = []
        for pack_id, pack in catalog.items():
            header_id = self._group_id(pack_kind=pack_kind, pack_id=pack_id)
            option_rows.append(
                Option(
                    header_id,
                    pack.title,
                    False,
                    group=pack_id,
                    is_group_header=True,
                    label=pack.title,
                )
            )
            for index, practice in enumerate(pack.practices):
                option_id = self._practice_id(pack_kind=pack_kind, pack_id=pack_id, index=index)
                option_rows.append(Option(option_id, practice, False, group=pack_id))

        if not previous_selected and not user_modified:
            selected_ids = set()
        else:
            selected_ids = previous_selected

        page.options = [
            Option(
                option.id,
                option.title,
                option.id in selected_ids,
                group=option.group,
                is_group_header=option.is_group_header,
                label=option.label,
            )
            for option in option_rows
        ]
        self._refresh_group_headers(page)
        page.subtitle = "Select practices (space: toggle, g: group toggle, a/n: all/none)."
        self.cursor_index = min(self.cursor_index, max(0, len(page.options) - 1))

    def run(self) -> dict | None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("The review wizard requires an interactive terminal.")
        return curses.wrapper(self._run_curses)

    def _run_curses(self, stdscr: curses.window) -> dict | None:
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.noecho()
        curses.cbreak()
        try:
            while True:
                self._draw(stdscr)
                key = stdscr.getch()
                outcome = self._handle_key(key)
                if outcome == "quit":
                    return None
                if outcome == "submit":
                    return self._selected_payload()
        finally:
            curses.nocbreak()
            stdscr.keypad(False)
            curses.echo()
            curses.curs_set(1)

    def _current_page(self) -> Page:
        return self.pages[self.page_index]

    def _current_options(self) -> list[Option]:
        return self._current_page().options

    def _selected_payload(self) -> dict:
        payload: dict[str, object] = {}
        for page in self.pages:
            if page.key == "summary":
                continue
            if page.single_select:
                selected = next((option.id for option in page.options if option.selected), None)
                payload[page.key] = selected
            elif self._is_grouped_practice_page(page.key):
                payload[page.key] = [
                    option.id
                    for option in page.options
                    if option.selected and not option.is_group_header and self._parse_practice_id(option.id)
                ]
            elif page.key == "specialties":
                payload[page.key] = expand_specialty_hierarchy(
                    [option.id for option in page.options if option.selected]
                )
            else:
                payload[page.key] = [option.id for option in page.options if option.selected]
        payload["baselines"] = self._selected_pack_ids_from_practice_page(
            page_key="baseline_practices",
            expected_kind="baseline",
        )
        payload["languages"] = self._selected_pack_ids_from_practice_page(
            page_key="language_practices",
            expected_kind="language",
        )
        return payload

    def _handle_key(self, key: int) -> str | None:
        page = self._current_page()
        if key in (ord("q"), 27):
            return "quit"
        if page.key == "summary":
            if key in (ord("s"), ord("S"), curses.KEY_ENTER, 10, 13):
                if not self.pages[0].options or not any(option.selected for option in self.pages[0].options):
                    self.status_message = "Select at least one persona before starting the review."
                    return None
                return "submit"
            if key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8, ord("b"), ord("B")):
                self.page_index = max(0, self.page_index - 1)
                self.cursor_index = 0
                self.scroll_offset = 0
            return None

        options = self._current_options()
        if key in (curses.KEY_DOWN, ord("j")) and options:
            self.cursor_index = min(len(options) - 1, self.cursor_index + 1)
        elif key in (curses.KEY_UP, ord("k")) and options:
            self.cursor_index = max(0, self.cursor_index - 1)
        elif key == ord(" "):
            self._toggle_current()
            if page.key == "tools":
                self.tools_user_modified = True
            if page.key == "baseline_practices":
                self.baseline_practices_user_modified = True
            if page.key == "language_practices":
                self.language_practices_user_modified = True
        elif key in (curses.KEY_RIGHT, curses.KEY_ENTER, 10, 13, ord("l"), ord("\t")):
            self._advance()
        elif key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8, ord("h")):
            self._retreat()
        elif key in (ord("a"), ord("A")):
            self._select_all_current()
            if page.key == "tools":
                self.tools_user_modified = True
            if page.key == "baseline_practices":
                self.baseline_practices_user_modified = True
            if page.key == "language_practices":
                self.language_practices_user_modified = True
        elif key in (ord("n"), ord("N")):
            self._clear_current()
            if page.key == "tools":
                self.tools_user_modified = True
            if page.key == "baseline_practices":
                self.baseline_practices_user_modified = True
            if page.key == "language_practices":
                self.language_practices_user_modified = True
        elif self._is_grouped_practice_page(page.key) and key in (ord("g"), ord("G")):
            self._toggle_group_at_cursor()
            if page.key == "baseline_practices":
                self.baseline_practices_user_modified = True
            if page.key == "language_practices":
                self.language_practices_user_modified = True
        elif self._is_grouped_practice_page(page.key) and key == ord("]"):
            self._move_cursor_to_group_header(direction=1)
        elif self._is_grouped_practice_page(page.key) and key == ord("["):
            self._move_cursor_to_group_header(direction=-1)
        elif page.key == "tools" and key in (ord("t"), ord("T")):
            self.show_all_tools = not self.show_all_tools
            self._sync_tools_page()
        return None

    def _toggle_current(self) -> None:
        page = self._current_page()
        options = page.options
        if not options:
            return
        current = options[self.cursor_index]
        if current.is_group_header and self._is_grouped_practice_page(page.key):
            self._toggle_group_at_cursor()
            return
        if page.single_select:
            for option in options:
                option.selected = False
            current.selected = True
            return
        current.selected = not current.selected
        self._refresh_group_headers(page)
        if page.key == "specialties":
            self._sync_specialty_page(self.specialties_catalog)

    def _select_all_current(self) -> None:
        page = self._current_page()
        for option in page.options:
            if option.is_group_header:
                continue
            option.selected = True
        self._refresh_group_headers(page)
        if page.key == "specialties":
            self._sync_specialty_page(self.specialties_catalog)

    def _clear_current(self) -> None:
        page = self._current_page()
        for option in page.options:
            if option.is_group_header:
                continue
            option.selected = False
        self._refresh_group_headers(page)
        if page.key == "specialties":
            self._sync_specialty_page(self.specialties_catalog)

    def _toggle_group_at_cursor(self) -> None:
        page = self._current_page()
        if not self._is_grouped_practice_page(page.key):
            return
        options = page.options
        if not options:
            return
        current = options[self.cursor_index]
        group = current.group
        if current.is_group_header:
            parsed = self._parse_group_id(current.id)
            if parsed is not None:
                _kind, group = parsed
        if not group:
            return
        group_items = [option for option in options if option.group == group and not option.is_group_header]
        if not group_items:
            return
        should_select = not all(option.selected for option in group_items)
        for option in group_items:
            option.selected = should_select
        self._refresh_group_headers(page)

    def _advance(self) -> None:
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.cursor_index = 0
            self.scroll_offset = 0

    def _retreat(self) -> None:
        if self.page_index > 0:
            self.page_index -= 1
            self.cursor_index = 0
            self.scroll_offset = 0

    def _draw(self, stdscr: curses.window) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        page = self._current_page()

        title = "Code review wizard"
        stdscr.addnstr(1, 2, title, width - 4, curses.A_BOLD)
        stdscr.addnstr(2, 2, f"Target: {self.target}", width - 4)
        stdscr.addnstr(3, 2, page.title, width - 4, curses.A_UNDERLINE)
        stdscr.addnstr(4, 2, page.subtitle, width - 4)
        if page.key == "tools":
            stdscr.addnstr(
                5,
                2,
                "Live setup feedback will be shown with a braille spinner during install and verification.",
                width - 4,
                curses.A_BOLD,
            )

        if self.status_message:
            stdscr.addnstr(6 if page.key == "tools" else 5, 2, self.status_message, width - 4, curses.A_BOLD)
        else:
            stdscr.addnstr(6 if page.key == "tools" else 5, 2, "Use arrows, space, and Enter to navigate.", width - 4)

        content_top = 8 if page.key == "tools" else 7
        content_bottom = height - 4
        if page.key == "summary":
            self._draw_summary(stdscr, content_top, content_bottom, width)
        else:
            if page.key == "tools":
                self._sync_tools_page()
            if page.key == "specialties":
                self._sync_specialty_page(self.specialties_catalog)
            if page.key == "baseline_practices":
                self._sync_practice_page(
                    page_key="baseline_practices",
                    pack_kind="baseline",
                    catalog=self.baselines_catalog,
                    initial_selected=self.initial_baseline_practice_selected,
                    user_modified=self.baseline_practices_user_modified,
                )
            if page.key == "language_practices":
                self._sync_practice_page(
                    page_key="language_practices",
                    pack_kind="language",
                    catalog=self.languages_catalog,
                    initial_selected=self.initial_language_practice_selected,
                    user_modified=self.language_practices_user_modified,
                )
            self._draw_options(stdscr, page, content_top, content_bottom, width)
            selected_count, total_count = self._selection_progress(page)
            stdscr.addnstr(
                7 if page.key == "tools" else 6, 2, f"Selected: {selected_count}/{total_count}", width - 4, curses.A_DIM
            )

        footer = "q quit | Enter next/start | space toggle | a select all | n clear all | h/left back"
        if self._is_grouped_practice_page(page.key):
            footer += " | g toggle group | [/] jump group"
        if page.key == "tools":
            footer += " | t toggle all/recommended"
        stdscr.addnstr(height - 2, 2, footer, width - 4)
        stdscr.addnstr(height - 1, 2, f"Step {self.page_index + 1}/{len(self.pages)}", width - 4, curses.A_DIM)
        stdscr.refresh()

    def _draw_options(self, stdscr: curses.window, page: Page, top: int, bottom: int, width: int) -> None:
        visible_height = max(1, bottom - top)
        if self.cursor_index < self.scroll_offset:
            self.scroll_offset = self.cursor_index
        elif self.cursor_index >= self.scroll_offset + visible_height:
            self.scroll_offset = self.cursor_index - visible_height + 1

        for row, option in enumerate(page.options[self.scroll_offset : self.scroll_offset + visible_height]):
            screen_row = top + row
            if option.is_group_header:
                group_items = [item for item in page.options if item.group == option.group and not item.is_group_header]
                selected_count = sum(1 for item in group_items if item.selected)
                total = len(group_items)
                if total > 0 and selected_count == total:
                    prefix = "[x]"
                elif selected_count > 0:
                    prefix = "[-]"
                else:
                    prefix = "[ ]"
                line = f"{prefix} {option.title}"
                base_style = curses.A_BOLD
            else:
                prefix = "[x]" if option.selected else "[ ]"
                line = f"  {prefix} {option.title}"
                base_style = curses.A_NORMAL
            style = base_style | (
                curses.A_REVERSE if self.scroll_offset + row == self.cursor_index else curses.A_NORMAL
            )
            stdscr.addnstr(screen_row, 2, line, width - 4, style)

        if not page.options:
            stdscr.addnstr(top, 2, "No options available.", width - 4, curses.A_DIM)

    def _draw_summary(self, stdscr: curses.window, top: int, bottom: int, width: int) -> None:
        payload = self._selected_payload()
        lines = [
            f"Personas: {', '.join(payload['personas']) or 'none'}",
            f"Baselines: {', '.join(payload['baselines']) or 'none'}",
            f"Baseline practices: {len(payload.get('baseline_practices', []))}",
            f"Tools: {', '.join(payload['tools']) or 'none'}",
            f"Languages: {', '.join(payload['languages']) or 'none'}",
            f"Language practices: {len(payload.get('language_practices', []))}",
            f"Specialties: {', '.join(payload['specialties']) or 'none'}",
            f"Strategies: {', '.join(payload['strategies']) or 'none'}",
            "",
            "Press S or Enter to start the review, or left/backspace to go back.",
        ]
        for row, line in enumerate(lines):
            if top + row >= bottom:
                break
            stdscr.addnstr(top + row, 2, line, width - 4, curses.A_BOLD if row == 0 else curses.A_NORMAL)


def run_review_wizard(
    *,
    target: str,
    personas: dict[str, Persona],
    baselines: dict[str, Pack],
    tools: dict[str, ToolPack],
    languages: dict[str, Pack],
    specialties: dict[str, Pack],
    strategies: dict[str, Strategy],
    config: dict,
) -> dict | None:
    return ReviewWizard(
        target=target,
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=config,
    ).run()
