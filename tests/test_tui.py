from __future__ import annotations

from code_review.review_planner.catalog import build_dynamic_catalog
from code_review.review_planner.tui import ReviewWizard, render_setup_summary_lines


def _wizard(config: dict) -> ReviewWizard:
    personas, baselines, tools, languages, specialties, strategies = build_dynamic_catalog({})
    return ReviewWizard(
        target=".",
        personas=personas,
        baselines=baselines,
        tools=tools,
        languages=languages,
        specialties=specialties,
        strategies=strategies,
        config=config,
    )


def test_tools_page_defaults_to_language_informed_recommendations() -> None:
    wizard = _wizard({"languages": ["python"]})
    wizard._sync_tools_page()

    tools_page = wizard._page_for_key("tools")
    assert tools_page.title == "Allow tool execution and installation"
    assert "authorizes crk to install it" in tools_page.subtitle
    tool_ids = [option.id for option in wizard._page_for_key("tools").options]
    assert tool_ids == ["python-ruff", "python-pyrefly", "python-bandit", "python-radon"]
    assert all(option.selected for option in wizard._page_for_key("tools").options)


def test_tools_page_toggle_can_show_all_tools() -> None:
    wizard = _wizard({"languages": ["python"]})
    wizard._sync_tools_page()

    wizard.show_all_tools = True
    wizard._sync_tools_page()
    tool_ids = [option.id for option in wizard._page_for_key("tools").options]
    assert "js-biome" in tool_ids
    js_biome = next(option for option in wizard._page_for_key("tools").options if option.id == "js-biome")
    assert js_biome.selected is False


def test_tools_page_keeps_non_recommended_explicit_selections_visible() -> None:
    wizard = _wizard({"languages": ["python"], "tools": ["js-biome"]})
    wizard._sync_tools_page()

    tool_ids = [option.id for option in wizard._page_for_key("tools").options]
    assert "js-biome" in tool_ids
    selected = next(option for option in wizard._page_for_key("tools").options if option.id == "js-biome")
    assert selected.selected is True


def test_wizard_uses_practice_pages_instead_of_pack_pages() -> None:
    wizard = _wizard({})
    keys = [page.key for page in wizard.pages]
    assert "baselines" not in keys
    assert "languages" not in keys
    assert "baseline_practices" in keys
    assert "language_practices" in keys


def test_baseline_practices_page_defaults_to_all_practices_for_selected_baselines() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="baseline_practices",
        pack_kind="baseline",
        catalog=wizard.baselines_catalog,
        initial_selected=wizard.initial_baseline_practice_selected,
        user_modified=wizard.baseline_practices_user_modified,
    )

    options = wizard._page_for_key("baseline_practices").options
    assert options
    selected_ids = [option.id for option in options if option.selected and option.id.startswith("baseline::")]
    assert selected_ids
    assert all(item.startswith("baseline::methodology-core::") for item in selected_ids)


def test_language_practices_page_honors_saved_practice_selection() -> None:
    wizard = _wizard(
        {
            "languages": ["python"],
            "language_practices": ["language::python::1"],
        }
    )
    wizard._sync_practice_page(
        page_key="language_practices",
        pack_kind="language",
        catalog=wizard.languages_catalog,
        initial_selected=wizard.initial_language_practice_selected,
        user_modified=wizard.language_practices_user_modified,
    )

    options = wizard._page_for_key("language_practices").options
    selected = [option.id for option in options if option.selected and option.id.startswith("language::")]
    assert selected == ["language::python::1"]


def test_language_practices_page_defaults_to_none_when_not_preselected() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="language_practices",
        pack_kind="language",
        catalog=wizard.languages_catalog,
        initial_selected=wizard.initial_language_practice_selected,
        user_modified=wizard.language_practices_user_modified,
    )

    options = wizard._page_for_key("language_practices").options
    selected = [option.id for option in options if option.selected and option.id.startswith("language::")]
    assert selected == []


def test_selected_payload_derives_pack_ids_from_practice_choices() -> None:
    wizard = _wizard(
        {
            "baseline_practices": ["baseline::methodology-core::0"],
            "language_practices": ["language::python::1", "language::typescript::0"],
        }
    )
    wizard._sync_practice_page(
        page_key="baseline_practices",
        pack_kind="baseline",
        catalog=wizard.baselines_catalog,
        initial_selected=wizard.initial_baseline_practice_selected,
        user_modified=wizard.baseline_practices_user_modified,
    )
    wizard._sync_practice_page(
        page_key="language_practices",
        pack_kind="language",
        catalog=wizard.languages_catalog,
        initial_selected=wizard.initial_language_practice_selected,
        user_modified=wizard.language_practices_user_modified,
    )
    payload = wizard._selected_payload()
    assert payload["baselines"] == ["methodology-core"]
    assert payload["languages"] == ["python", "typescript"]


def test_practice_pages_render_group_headers() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="baseline_practices",
        pack_kind="baseline",
        catalog=wizard.baselines_catalog,
        initial_selected=wizard.initial_baseline_practice_selected,
        user_modified=wizard.baseline_practices_user_modified,
    )

    options = wizard._page_for_key("baseline_practices").options
    assert options[0].is_group_header is True
    assert options[0].id.startswith("group::baseline::")
    assert "::" not in options[1].title


def test_group_toggle_selects_all_items_in_same_pack() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="baseline_practices",
        pack_kind="baseline",
        catalog=wizard.baselines_catalog,
        initial_selected=wizard.initial_baseline_practice_selected,
        user_modified=wizard.baseline_practices_user_modified,
    )
    page = wizard._page_for_key("baseline_practices")
    wizard.page_index = next(
        index for index, item in enumerate(wizard.pages) if item.key == "baseline_practices"
    )
    wizard.cursor_index = 0
    wizard._clear_current()

    wizard._toggle_group_at_cursor()
    group = page.options[0].group
    assert group
    children = [option for option in page.options if option.group == group and not option.is_group_header]
    assert children
    assert all(option.selected for option in children)


def test_group_jump_moves_cursor_to_next_header() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="language_practices",
        pack_kind="language",
        catalog=wizard.languages_catalog,
        initial_selected=wizard.initial_language_practice_selected,
        user_modified=wizard.language_practices_user_modified,
    )
    wizard.page_index = next(index for index, item in enumerate(wizard.pages) if item.key == "language_practices")
    wizard.cursor_index = 0

    wizard._handle_key(ord("]"))

    page = wizard._page_for_key("language_practices")
    assert wizard.cursor_index > 0
    assert page.options[wizard.cursor_index].is_group_header is True


def test_selection_progress_ignores_group_headers() -> None:
    wizard = _wizard({})
    wizard._sync_practice_page(
        page_key="baseline_practices",
        pack_kind="baseline",
        catalog=wizard.baselines_catalog,
        initial_selected=wizard.initial_baseline_practice_selected,
        user_modified=wizard.baseline_practices_user_modified,
    )
    page = wizard._page_for_key("baseline_practices")
    selected_count, total_count = wizard._selection_progress(page)
    non_headers = [option for option in page.options if not option.is_group_header]
    assert total_count == len(non_headers)
    assert selected_count == len([option for option in non_headers if option.selected])


def test_select_all_on_personas_does_not_propagate_to_language_practices() -> None:
    wizard = _wizard({})
    wizard.page_index = 0
    wizard._handle_key(ord("a"))

    wizard._sync_practice_page(
        page_key="language_practices",
        pack_kind="language",
        catalog=wizard.languages_catalog,
        initial_selected=wizard.initial_language_practice_selected,
        user_modified=wizard.language_practices_user_modified,
    )
    language_page = wizard._page_for_key("language_practices")
    selected_language = [option for option in language_page.options if option.selected and option.id.startswith("language::")]
    assert selected_language == []


def test_render_setup_summary_lines_uses_tables() -> None:
    lines = render_setup_summary_lines(
        plan={
            "target": "/repo",
            "selections": {
                "personas": ["correctness"],
                "baselines": ["methodology-core"],
                "tools": ["python-ruff"],
                "languages": ["python"],
                "specialties": [],
                "strategies": [],
            },
        },
        tool_setup_results=[
            {
                "id": "python-ruff",
                "status": "passed",
                "steps": [
                    {"kind": "prereq", "text": "uv installed", "status": "passed"},
                    {"kind": "verify", "text": "uvx ruff --version", "status": "passed"},
                ],
            }
        ],
        tool_setup_error=None,
    )

    assert "| Setting" in "\n".join(lines)
    assert "| Tool" in "\n".join(lines)
    assert lines[-1] == "✅ Setup complete. Press Enter to exit."


def test_specialty_page_groups_core_and_overlays() -> None:
    wizard = _wizard({"specialties": ["ui-ux-cli-tui"]})
    wizard._sync_specialty_page(wizard.specialties_catalog)

    page = wizard._page_for_key("specialties")
    ids = [option.id for option in page.options]
    ui_ux_index = ids.index("ui-ux")
    assert ids[ui_ux_index : ui_ux_index + 3] == ["ui-ux", "ui-ux-cli-tui", "ui-ux-web"]
    selected = [option.id for option in page.options if option.selected]
    assert "ui-ux" in selected
    assert "ui-ux-cli-tui" in selected
