import { useState } from "react";
import {
  Callout, CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramLane, DiagramNode,
  Lead, LinkedNotes, RichHero, RichLesson, Section,
} from "../../shared";

const catalogDemo = [
  "from contextlib import closing",
  "from pathlib import Path",
  "from tempfile import TemporaryDirectory",
  "import json",
  "import sqlite3",
  "",
  "",
  "def init_catalog(database):",
  "    with closing(sqlite3.connect(database)) as connection, connection:",
  "        connection.execute(\"CREATE TABLE IF NOT EXISTS equipment (id INTEGER PRIMARY KEY, label TEXT)\")",
  "",
  "",
  "def read_catalog(database):",
  "    with closing(sqlite3.connect(database)) as connection:",
  "        return connection.execute(\"SELECT id, label FROM equipment ORDER BY id\").fetchall()",
  "",
  "",
  "def write_record(database, record):",
  "    with closing(sqlite3.connect(database)) as connection, connection:",
  "        connection.execute(\"INSERT INTO equipment VALUES (?, ?)\", record)",
  "",
  "",
  "def import_archive(archive, target):",
  "    if not target.is_file():",
  "        raise sqlite3.OperationalError(\"Файл цели недоступен\")",
  "    records = json.loads(archive.read_text(encoding=\"utf-8\"))",
  "    with closing(sqlite3.connect(target)) as connection, connection:",
  "        if connection.execute(\"SELECT 1 FROM equipment LIMIT 1\").fetchone():",
  "            raise ValueError(\"Цель уже содержит записи\")",
  "        connection.executemany(",
  "            \"INSERT INTO equipment VALUES (?, ?)\",",
  "            [(record[\"id\"], record[\"label\"]) for record in records],",
  "        )",
  "",
  "",
  "with TemporaryDirectory() as folder:",
  "    root = Path(folder)",
  "    working = root / \"club.sqlite3\"",
  "    target = root / \"import.sqlite3\"",
  "    archive = root / \"club-archive.json\"",
  "    init_catalog(working)",
  "    write_record(working, (4, \"Проектор\"))",
  "    print(\"Обычное чтение без архива:\", read_catalog(working))",
  "",
  "    archive.write_text('[{\"id\": 8, \"label\": \"Радиомикрофон\"}]', encoding=\"utf-8\")",
  "    before = archive.read_bytes()",
  "    unavailable = root / \"missing\" / \"club.sqlite3\"",
  "    for name, operation in (",
  "        (\"Чтение\", lambda: read_catalog(unavailable)),",
  "        (\"Запись\", lambda: write_record(unavailable, (5, \"Штатив\"))),",
  "    ):",
  "        try:",
  "            operation()",
  "        except sqlite3.OperationalError:",
  "            print(name + \": операция остановилась\")",
  "    print(\"Байты архива совпадают:\", archive.read_bytes() == before)",
  "",
  "    init_catalog(target)",
  "    print(\"Пустая цель после init и обычного чтения:\", read_catalog(target))",
  "    import_archive(archive, target)",
  "    print(\"После явного импорта:\", read_catalog(target))",
].join("\n");

const sqlOnlyCheck = [
  "from argparse import ArgumentParser",
  "import ast",
  "from contextlib import closing",
  "import json",
  "import os",
  "from pathlib import Path",
  "import re",
  "import shutil",
  "import sqlite3",
  "import subprocess",
  "import sys",
  "from tempfile import TemporaryDirectory",
  "",
  "",
  "ROOT = Path.cwd()",
  "ARCHIVE_ROW = {",
  "    \"id\": 41, \"title\": \"Архивная метка\", \"priority\": 2,",
  "    \"is_done\": False, \"tags\": [\"архив\"],",
  "}",
  "TITLE = \"Сверить этикетки\"",
  "LEGACY = re.compile(",
  "    r\"\\b(?:JsonStorage|MemoryStorage|JsonTaskRepository|DEFAULT_JSON_PATH|\"",
  "    r\"JSON_PATH|json_path|json_service_context)\\b|(?:\\bapp|from \\.)\\.?storage\\b\"",
  ")",
  "OLD_TEST = re.compile(",
  "    LEGACY.pattern + r\"|\\.repository\\.storage\\b|legacy_json|JSON task\"",
  "    r\"|build_service\\(\\s*(?:settings=|[^),]*(?:storage|observed))\"",
  ")",
  "REPLACE_TEST_CASES = {",
  "    \"test_api.py\", \"test_cli.py\", \"test_cli_regression.py\",",
  "    \"test_cli_session_scope.py\", \"test_config.py\", \"test_contract.py\",",
  "    \"test_dependencies.py\", \"test_json_repository.py\", \"test_json_storage.py\",",
  "    \"test_preferences.py\", \"test_services.py\", \"test_sql_create.py\",",
  "    \"test_sql_runtime.py\", \"test_storage.py\", \"test_put_replace_lab.py\",",
  "    \"test_put_replace_local.py\", \"test_sql_patch_lab.py\", \"test_sql_readiness.py\",",
  "}",
  "ARCHIVE_TEST_CASES = {",
  "    \"test_cli.py\": {",
  "        \"test_run_routes_list_stats_done_unknown_and_exit\",",
  "        \"test_add_find_search_and_delete_use_passed_callbacks\",",
  "        \"test_nonnumeric_and_missing_ids_show_errors_without_saving\",",
  "        \"test_storage_error_from_load_is_not_hidden\",",
  "        \"test_storage_error_from_save_is_not_reported_as_success\",",
  "        \"test_main_reports_storage_error_at_existing_boundary\",",
  "    },",
  "    \"test_cli_regression.py\": {",
  "        \"test_add_retries_invalid_title_and_priority\",",
  "        \"test_all_commands_and_unknown_command_keep_their_contracts\",",
  "        \"test_observed_storage_counts_and_delegates_add\",",
  "        \"test_only_real_changes_save_and_read_or_invalid_commands_do_not\",",
  "        \"test_storage_errors_and_unexpected_defects_remain_visible\",",
  "        \"test_build_service_preserves_falsey_storage_without_using_it\",",
  "        \"test_default_build_and_subprocess_import_are_inert\",",
  "        \"test_list_does_not_save\",",
  "    },",
  "    \"test_services.py\": {",
  "        \"test_add_task_returns_normalized_task_and_lists_it\",",
  "        \"test_get_task_returns_the_matching_task\",",
  "        \"test_search_tasks_ignores_case\",",
  "        \"test_search_tasks_returns_empty_list_without_match\",",
  "        \"test_mark_done_is_idempotent\",",
  "        \"test_delete_task_keeps_neighbor_ids\",",
  "        \"test_statistics_count_all_open_and_done_tasks\",",
  "        \"test_missing_valid_id_raises_without_changing_tasks\",",
  "        \"test_invalid_id_raises_value_error_without_changing_tasks\",",
  "        \"test_mark_done_changes_only_selected_task\",",
  "        \"test_replace_task_preserves_id_tags_and_neighbor\",",
  "        \"test_select_tasks_filters_sorts_and_limits_without_saving\",",
  "    },",
  "    \"test_storage.py\": {",
  "        \"test_memory_storage_copies_constructor_list\",",
  "        \"test_memory_storage_copies_saved_list\",",
  "        \"test_memory_storage_load_returns_a_new_list\",",
  "        \"test_memory_storage_shares_task_objects_between_lists\",",
  "    },",
  "    \"test_json_repository.py\": {",
  "        \"test_repository_reads_preserve_planner_results_and_order\",",
  "        \"test_service_delegates_reads_and_keeps_id_errors_at_service_boundary\",",
  "        \"test_service_writes_and_external_save_are_visible_to_fresh_repository_reads\",",
  "        \"test_repository_writes_keep_neighbors_order_and_save_only_found_changes\",",
  "        \"test_service_routes_mutations_through_repository_and_skips_noop_saves\",",
  "    },",
  "    \"test_json_storage.py\": {",
  "        \"test_missing_json_file_loads_empty_without_creating_file\",",
  "        \"test_round_trip_restores_task_fields_and_next_id\",",
  "        \"test_legacy_records_without_tags_load_independent_empty_tags\",",
  "        \"test_legacy_record_missing_id_raises_storage_error\",",
  "        \"test_invalid_json_files_raise_without_changing_bytes\",",
  "        \"test_directory_path_raises_storage_error_without_removing_directory\",",
  "    },",
  "    \"test_sql_readiness.py\": set(),",
  "}",
  "",
  "CLI_TESTS = r'''\"\"\"Публичная CLI-регрессия на временной SQLite.\"\"\"",
  "import pytest",
  "from sqlalchemy import event",
  "from sqlalchemy.exc import SQLAlchemyError",
  "",
  "from app.DBase.models import Base",
  "from app.DBase.session import create_session_factory",
  "from app.cli import run",
  "from app.core.engine import make_engine",
  "from app.main import sql_service_context",
  "",
  "",
  "@pytest.fixture",
  "def cli_database(tmp_path):",
  "    address = \"sqlite:///\" + (tmp_path / \"cli.sqlite3\").as_posix()",
  "    engine = make_engine(address)",
  "    Base.metadata.create_all(engine)",
  "    writes = []",
  "",
  "    @event.listens_for(engine, \"before_cursor_execute\")",
  "    def record_write(connection, cursor, statement, parameters, context, many):",
  "        operation = statement.strip().split(maxsplit=1)[0].upper()",
  "        if operation in {\"INSERT\", \"UPDATE\", \"DELETE\"}:",
  "            writes.append(operation)",
  "",
  "    factory = create_session_factory(engine)",
  "    try:",
  "        yield factory, writes",
  "    finally:",
  "        engine.dispose()",
  "",
  "",
  "def run_cli(factory, answers):",
  "    remaining = iter(answers)",
  "    messages = []",
  "    run(",
  "        read=lambda _prompt: next(remaining),",
  "        write=messages.append,",
  "        context_factory=lambda: sql_service_context(factory),",
  "    )",
  "    assert next(remaining, None) is None",
  "    return messages",
  "",
  "",
  "def test_sql_cli_commands_and_input(cli_database):",
  "    factory, writes = cli_database",
  "    messages = run_cli(factory, [",
  "        \"list\", \"add\", \" \", \"  Write docs  \", \"x\", \"6\", \"4\",",
  "        \"find\", \"x\", \"1\", \"done\", \"1\", \"done\", \"1\", \"list\",",
  "        \"search\", \" \", \"search\", \"DOC\", \"stats\", \"unknown\",",
  "        \"delete\", \"1\", \"list\", \"exit\",",
  "    ])",
  "    assert messages.count(\"Список задач пуст.\") == 2",
  "    assert \"Название не может быть пустым.\" in messages",
  "    assert \"Введите целое число от 1 до 5.\" in messages",
  "    assert \"Приоритет должен быть от 1 до 5.\" in messages",
  "    assert \"Введите целый положительный номер задачи.\" in messages",
  "    assert messages.count(\"[1] Write docs | приоритет: 4 | не завершена\") == 2",
  "    assert messages.count(\"Задача выполнена.\") == 2",
  "    assert messages.count(\"[1] Write docs | приоритет: 4 | выполнена\") == 2",
  "    assert \"Введите фрагмент названия.\" in messages",
  "    assert \"Всего: 1\" in messages",
  "    assert \"Выполнено: 1\" in messages",
  "    assert \"Осталось: 0\" in messages",
  "    assert \"Процент выполнения: 100.0%\" in messages",
  "    assert \"Неизвестная команда.\" in messages",
  "    assert \"Задача удалена.\" in messages",
  "    assert writes == [\"INSERT\", \"UPDATE\", \"DELETE\"]",
  "    with sql_service_context(factory) as planner:",
  "        assert planner.list_tasks() == []",
  "",
  "",
  "def test_missing_id_and_search_do_not_write(cli_database):",
  "    factory, writes = cli_database",
  "    run_cli(factory, [\"add\", \"Keep this\", \"2\", \"exit\"])",
  "    writes.clear()",
  "    messages = run_cli(factory, [",
  "        \"find\", \"999\", \"done\", \"999\", \"delete\", \"999\",",
  "        \"search\", \"missing\", \"list\", \"stats\", \"exit\",",
  "    ])",
  "    assert messages.count(\"Задача не найдена.\") == 3",
  "    assert \"Совпадений не найдено.\" in messages",
  "    assert \"[1] Keep this | приоритет: 2 | не завершена\" in messages",
  "    assert \"Осталось: 1\" in messages",
  "    assert writes == []",
  "    with sql_service_context(factory) as planner:",
  "        assert [(task.title, task.priority, task.is_done) for task in planner.list_tasks()] == [",
  "            (\"Keep this\", 2, False),",
  "        ]",
  "",
  "",
  "@pytest.mark.parametrize(\"answers\", [[\"list\"], [\"add\", \"New task\", \"3\"]])",
  "def test_unavailable_sqlite_stops_cli_without_success(tmp_path, answers):",
  "    address = \"sqlite:///\" + (tmp_path / \"no-parent\" / \"unavailable.sqlite3\").as_posix()",
  "    engine = make_engine(address)",
  "    factory = create_session_factory(engine)",
  "    remaining = iter([*answers, \"exit\"])",
  "    messages = []",
  "    try:",
  "        try:",
  "            run(",
  "                read=lambda _prompt: next(remaining),",
  "                write=messages.append,",
  "                context_factory=lambda: sql_service_context(factory),",
  "            )",
  "        except SQLAlchemyError:",
  "            assert len(messages) == 1 and messages[0].startswith(\"Команды:\")",
  "        else:",
  "            assert any(\"команда не выполнена\" in line.casefold() for line in messages)",
  "            assert all(not line.startswith(\"[\") and line != \"Список задач пуст.\"",
  "                       for line in messages)",
  "    finally:",
  "        engine.dispose()",
  "'''",
  "",
  "SQL_FIXTURES = \"\"\"",
  "@pytest.fixture",
  "def sql_fixture(tmp_path):",
  "    from app.DBase.models import Base",
  "    from app.DBase.session import create_session_factory",
  "    from app.core.engine import make_engine",
  "    from app.main import sql_service_context",
  "",
  "    url = \"sqlite:///\" + (tmp_path / \"fixture.sqlite3\").as_posix()",
  "    engine = make_engine(url)",
  "    Base.metadata.create_all(engine)",
  "    factory = create_session_factory(engine)",
  "    try:",
  "        yield lambda: sql_service_context(factory)",
  "    finally:",
  "        engine.dispose()",
  "",
  "",
  "@pytest.fixture",
  "def api_service(sql_fixture):",
  "    with sql_fixture() as planner:",
  "        yield planner",
  "",
  "",
  "@pytest.fixture",
  "def api_client(sql_fixture):",
  "    previous = app.dependency_overrides.copy()",
  "",
  "    def provide_planner():",
  "        with sql_fixture() as planner:",
  "            yield planner",
  "",
  "    app.dependency_overrides[get_planner] = provide_planner",
  "    try:",
  "        with TestClient(app) as client:",
  "            yield client",
  "    finally:",
  "        app.dependency_overrides.clear()",
  "        app.dependency_overrides.update(previous)",
  "\"\"\".strip()",
  "",
  "",
  "def require(condition, message):",
  "    if not condition:",
  "        raise RuntimeError(message)",
  "",
  "",
  "def source(path):",
  "    return path.read_text(encoding=\"utf-8-sig\")",
  "",
  "",
  "def outline():",
  "    for path in sorted((ROOT / \"app\").rglob(\"*.py\")):",
  "        code = source(path)",
  "        visible = re.compile(",
  "            LEGACY.pattern + r\"|def (?:build_service|get_planner|sql_service_context|\"",
  "            r\"make_http_planner|make_cli_context|main)\\(|(?:build_service|\"",
  "            r\"SqlTaskRepository|make_http_planner|make_cli_context|PlannerService)\\(\"",
  "            r\"|context_factory=\"",
  "        )",
  "        for number, line in enumerate(code.splitlines(), 1):",
  "            if visible.search(line):",
  "                print(f\"{path.relative_to(ROOT)}:{number}  {line.strip()}\")",
  "",
  "",
  "def edit_nodes(code, edits):",
  "    lines = code.splitlines(keepends=True)",
  "    for node, replacement in sorted(edits, key=lambda edit: edit[0].lineno, reverse=True):",
  "        first = min([node.lineno] + [x.lineno for x in getattr(node, \"decorator_list\", [])])",
  "        lines[first - 1:node.end_lineno] = replacement.splitlines(keepends=True)",
  "    return \"\".join(lines)",
  "",
  "",
  "def remove_nodes(code, nodes):",
  "    return edit_nodes(code, [(node, \"\") for node in nodes])",
  "",
  "",
  "def without_old_imports(code):",
  "    imports = [",
  "        node for node in ast.parse(code).body",
  "        if isinstance(node, ast.ImportFrom) and node.module == \"app.storage\"",
  "    ]",
  "    return remove_nodes(code, imports)",
  "",
  "",
  "def names_in(code):",
  "    return {node.id for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Name)}",
  "",
  "",
  "def clean_config(code):",
  "    if not LEGACY.search(code):",
  "        return code",
  "    pattern = (",
  "        r'(?ms)^    default_json_path = root / \"data\" / \"tasks\\.json\"\\n'",
  "        r'\\n    raw_path = values\\.get\\(\"JSON_PATH\"\\).*?'",
  "        r'(?=^    raw_app_name = )'",
  "    )",
  "    code, count = re.subn(pattern, \"\", code, count=1)",
  "    require(count == 1, \"app/core/config.py: знакомый блок JSON_PATH не найден\")",
  "    code = re.sub(r\"(?m)^(?:DEFAULT_JSON_PATH = .*|    json_path: Path|\"",
  "                  r\"        json_path=json_path,)\\n\", \"\", code)",
  "    require(not LEGACY.search(code), \"app/core/config.py: остались ссылки на JSON\")",
  "    return code",
  "",
  "",
  "def clean_fixture(code):",
  "    tree = ast.parse(code)",
  "    fixtures = [node for node in tree.body if isinstance(node, ast.FunctionDef)",
  "                and node.name in {\"api_service\", \"api_client\"}]",
  "    require({node.name for node in fixtures} == {\"api_service\", \"api_client\"}",
  "            and \"JsonStorage\" in code, \"tests/conftest.py: формат фикстур отличается\")",
  "    code = without_old_imports(remove_nodes(code, fixtures))",
  "    if \"build_service\" not in names_in(code):",
  "        code = re.sub(r\"(?m)^from app\\.main import build_service\\n\", \"\", code)",
  "    return code.rstrip() + \"\\n\\n\\n\" + SQL_FIXTURES + \"\\n\"",
  "",
  "",
  "def update_known_regression(path, node, block):",
  "    case = path.name, node.name",
  "    if case == (\"test_dependencies.py\", \"test_get_planner_returns_service_from_current_request_app\"):",
  "        legacy = '''def test_get_planner_returns_service_from_current_request_app():",
  "    application = FastAPI()",
  "    expected_planner = object()",
  "    application.state.planner = expected_planner",
  "    request = Request({\"type\": \"http\", \"app\": application})",
  "",
  "    assert get_planner(request) is expected_planner'''",
  "        require(block == legacy and not node.decorator_list,",
  "                f\"{path}: проверьте пользовательскую версию теста get_planner\")",
  "        return '''def test_get_planner_uses_the_supplied_session(sql_fixture):",
  "    with sql_fixture() as original:",
  "        session = original.repository.session",
  "        planner = get_planner(session)",
  "        assert planner.repository.session is session",
  "        assert planner.write_scope == session.begin",
  "        saved = planner.add_task(\"Проверка зависимости\", 2)",
  "    with sql_fixture() as fresh:",
  "        assert fresh.get_task(saved.id).to_dict() == saved.to_dict()'''",
  "",
  "    def swap(source, old, new, count=1):",
  "        require(source.count(old) == count,",
  "                f\"{path}: не узнан участок теста {node.name}, нужна ручная сверка\")",
  "        return source.replace(old, new, count)",
  "",
  "    if not OLD_TEST.search(block):",
  "        return None",
  "    if case == (\"test_api.py\", \"test_item_returns_public_task_without_tags\"):",
  "        block = swap(block, \"(api_client, api_service):\", \"(api_client, api_service, sql_fixture):\")",
  "        return swap(block, '''    task = api_service.add_task(\"Read item\", 4)",
  "    stored_tasks = api_service.repository.storage.load()",
  "    stored_tasks[0].tags.append(\"internal\")",
  "    api_service.repository.storage.save(stored_tasks)''', '''    task = api_service.add_task(\"Read item\", 4, tags=[\"internal\"])",
  "    with sql_fixture() as fresh:",
  "        assert fresh.get_task(task.id).tags == [\"internal\"]''')",
  "    if case == (\"test_preferences.py\", \"test_cookie_round_trip_delete_client_isolation_and_planner_regression\"):",
  "        block = swap(block, \"    api_client, api_service\\n\", \"    api_client, api_service, sql_fixture\\n\")",
  "        block = swap(block, \"    json_before = api_service.repository.storage.path.read_bytes()\", '''    def saved_tasks():",
  "        with sql_fixture() as fresh:",
  "            return [task.to_dict() for task in fresh.list_tasks()]",
  "",
  "    sql_before = saved_tasks()''')",
  "        return swap(block, \"    assert api_service.repository.storage.path.read_bytes() == json_before\",",
  "                    \"    assert saved_tasks() == sql_before\", count=2)",
  "    return None",
  "",
  "",
  "def clean_test(path, code):",
  "    if path.name not in REPLACE_TEST_CASES and not path.name.startswith(\"test_put_replace_local\"):",
  "        return code",
  "    tree = ast.parse(code)",
  "    has_legacy = bool(OLD_TEST.search(code))",
  "    if path.name in ARCHIVE_TEST_CASES:",
  "        if not has_legacy:",
  "            return code",
  "        names = {node.name for node in ast.walk(tree)",
  "                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(\"test_\")}",
  "        extra = names - ARCHIVE_TEST_CASES[path.name]",
  "        require(not extra,",
  "                f\"{path}: проверьте свои тесты перед архивом: {', '.join(sorted(extra))}\")",
  "        if path.name in {\"test_cli.py\", \"test_cli_regression.py\"}:",
  "            print(\"  CLI-поведение перенесено в tests/test_sql_only_cli.py:\", path)",
  "        return None",
  "    updates = []",
  "    for node in tree.body:",
  "        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(\"test_\"):",
  "            continue",
  "        block = ast.get_source_segment(code, node) or \"\"",
  "        replacement = update_known_regression(path, node, block)",
  "        if replacement is not None:",
  "            require(not node.decorator_list and not OLD_TEST.search(replacement),",
  "                    f\"{path}: проверьте расширения теста {node.name} перед переносом\")",
  "            print(\"  SQL-регрессия сохранена:\", path, node.name)",
  "            updates.append((node, replacement + \"\\n\"))",
  "            continue",
  "        former_contract = any(fragment in node.name for fragment in (",
  "            \"json_as_default\", \"json_remains_default\", \"one_json\",",
  "            \"uses_settings_and_preserves_explicit_storage\",",
  "        ))",
  "        flags_old_api = node.name == \"test_build_service_accepts_explicit_repository_without_loading_settings\"",
  "        if OLD_TEST.search(block) or (has_legacy and (former_contract or flags_old_api)):",
  "            print(\"  Перенос сценария в резерв:\", path, node.name)",
  "            updates.append((node, \"\"))",
  "    if not updates and not has_legacy:",
  "        return code",
  "    code = without_old_imports(edit_nodes(code, updates))",
  "    remaining_names = names_in(code)",
  "    require(not ({\"JsonStorage\", \"MemoryStorage\", \"JsonTaskRepository\"} & remaining_names),",
  "            f\"{path}: старый ресурс используется за пределами известных сценариев\")",
  "    return code",
  "",
  "",
  "def cleanup():",
  "    backup = ROOT / \"sql-only-backup\"",
  "    require(not backup.exists(), \"Папка sql-only-backup уже есть. Сначала прочитайте список прежних правок.\")",
  "    edits = {}",
  "    for relative in (\"app/main.py\", \"app/service.py\"):",
  "        code = source(ROOT / relative)",
  "        old_helper = [",
  "            node for node in ast.parse(code).body",
  "            if isinstance(node, ast.FunctionDef) and node.name == \"json_service_context\"",
  "        ]",
  "        code = without_old_imports(remove_nodes(code, old_helper))",
  "        if \"Settings\" not in names_in(code):",
  "            code = re.sub(r\"(?m)^from app\\.core\\.config import Settings, load_settings$\",",
  "                          \"from app.core.config import load_settings\", code)",
  "        require(not LEGACY.search(code) and not re.search(r\"\\bstorage\\b\", code),",
  "                f\"{relative}: сначала уберите зависимость сборки самостоятельно\")",
  "        edits[Path(relative)] = code",
  "    config = Path(\"app/core/config.py\")",
  "    edits[config] = clean_config(source(ROOT / config))",
  "    env_file = Path(\".env.example\")",
  "    if (ROOT / env_file).is_file():",
  "        edits[env_file] = re.sub(r\"(?m)^[ \\t]*JSON_PATH\\s*=.*\\n?\", \"\", source(ROOT / env_file))",
  "    storage = Path(\"app/storage.py\")",
  "    if (ROOT / storage).is_file():",
  "        edits[storage] = None",
  "",
  "    for path in sorted((ROOT / \"tests\").glob(\"*.py\")):",
  "        relative = path.relative_to(ROOT)",
  "        code = source(path)",
  "        if path.name == \"conftest.py\" and LEGACY.search(code):",
  "            edits[relative] = clean_fixture(code)",
  "        elif path.name.startswith(\"test_\"):",
  "            replacement = clean_test(relative, code)",
  "            if replacement != code:",
  "                edits[relative] = replacement",
  "",
  "    for tree in (\"app\", \"tests\"):",
  "        for path in (ROOT / tree).rglob(\"*.py\"):",
  "            relative = path.relative_to(ROOT)",
  "            code = edits.get(relative, source(path))",
  "            if code is not None:",
  "                ast.parse(code, filename=str(relative))",
  "                if tree == \"app\":",
  "                    require(not LEGACY.search(code),",
  "                            f\"{relative}: вне шаблона осталась ссылка на старую сборку\")",
  "                else:",
  "                    require(\"from app.storage import\" not in code,",
  "                            f\"{relative}: старый импорт вне известных шаблонов\")",
  "",
  "    edits = {path: code for path, code in edits.items() if code != source(ROOT / path)}",
  "    new_tests = Path(\"tests/test_sql_only_cli.py\")",
  "    destination = ROOT / new_tests",
  "    test_module = CLI_TESTS.strip() + \"\\n\"",
  "    if destination.exists():",
  "        require(source(destination) == test_module,",
  "                f\"{new_tests}: имя уже занято, файлы не изменены\")",
  "    else:",
  "        ast.parse(test_module)",
  "    if not edits and destination.is_file():",
  "        print(\"Механических замен нет.\")",
  "        return",
  "    print(\"Список механических правок:\")",
  "    for path, code in sorted(edits.items()):",
  "        print(\" \", path, \"→\", \"в архив исходников\" if code is None else \"готовая замена\")",
  "    saved_paths = {path: (backup / path).with_suffix(path.suffix + \".saved\") for path in edits}",
  "    for path in edits:",
  "        resolved = (ROOT / path).resolve()",
  "        require(resolved.is_relative_to(ROOT) and not (ROOT / path).is_symlink(),",
  "                f\"{path}: шаблон работает только с обычными файлами проекта\")",
  "    for path, saved in saved_paths.items():",
  "        saved.parent.mkdir(parents=True, exist_ok=True)",
  "        shutil.copy2(ROOT / path, saved)",
  "    for path, saved in saved_paths.items():",
  "        require((ROOT / path).read_bytes() == saved.read_bytes(), f\"{path}: файл изменился до замены\")",
  "    applied = []",
  "    try:",
  "        for path, code in edits.items():",
  "            applied.append(path)",
  "            if code is None:",
  "                (ROOT / path).unlink()",
  "            else:",
  "                (ROOT / path).write_text(code, encoding=\"utf-8\")",
  "        if not destination.is_file():",
  "            with destination.open(\"x\", encoding=\"utf-8\") as output:",
  "                output.write(test_module)",
  "            print(\"  Готовая SQL-регрессия CLI:\", new_tests)",
  "    except OSError:",
  "        for path in reversed(applied):",
  "            shutil.copy2(saved_paths[path], ROOT / path)",
  "        raise",
  "    print(\"Исходники правок сохранены в sql-only-backup/. Данные не менялись.\")",
  "",
  "",
  "CHILD = r'''",
  "import atexit",
  "import json",
  "import os",
  "import runpy",
  "import sys",
  "",
  "archive = os.path.normcase(os.path.abspath(os.environ[\"SQL_ONLY_ARCHIVE\"]))",
  "opened = []",
  "",
  "def observe(event, args):",
  "    if event == \"open\" and isinstance(args[0], (str, bytes)):",
  "        path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))",
  "        if path == archive:",
  "            opened.append(path)",
  "",
  "sys.addaudithook(observe)",
  "atexit.register(lambda: print(\"\\nARCHIVE_OPENS=\" + str(len(opened))))",
  "",
  "mode = sys.argv[1]",
  "if mode in (\"init\", \"cli\", \"import\"):",
  "    modules = {",
  "        \"init\": \"app.DBase.init_db\",",
  "        \"cli\": \"app.main\",",
  "        \"import\": \"app.DBase.import_tasks\",",
  "    }",
  "    sys.argv = [modules[mode], *sys.argv[2:]]",
  "    runpy.run_module(modules[mode], run_name=\"__main__\")",
  "else:",
  "    from fastapi.testclient import TestClient",
  "    from app.api import app",
  "",
  "    calls = json.loads(sys.argv[2])",
  "    rows = []",
  "    with TestClient(app, raise_server_exceptions=False) as client:",
  "        for method, path, body in calls:",
  "            response = client.request(method, path, json=body)",
  "            try:",
  "                data = response.json()",
  "            except ValueError:",
  "                data = response.text",
  "            rows.append([response.status_code, data])",
  "    print(\"HTTP_OBSERVATION=\" + json.dumps(rows, ensure_ascii=False))",
  "'''",
  "",
  "",
  "def database_env(lab, database, marker):",
  "    return {",
  "        **os.environ,",
  "        \"PYTHONPATH\": str(lab),",
  "        \"PYTHONIOENCODING\": \"utf-8\",",
  "        \"DATABASE_URL\": \"sqlite:///\" + database.as_posix(),",
  "        \"JSON_PATH\": str(marker),",
  "        \"SQL_ONLY_ARCHIVE\": str(marker),",
  "        \"COOKIE_SECURE\": \"false\",",
  "    }",
  "",
  "",
  "def run_process(lab, database, marker, mode, args=(), typed=\"\"):",
  "    return subprocess.run(",
  "        [sys.executable, \"-c\", CHILD, mode, *args],",
  "        cwd=lab, env=database_env(lab, database, marker),",
  "        input=typed, encoding=\"utf-8\", errors=\"replace\",",
  "        capture_output=True, timeout=30, check=False,",
  "    )",
  "",
  "",
  "def archive_opens(result):",
  "    hits = re.findall(r\"(?m)^ARCHIVE_OPENS=(\\d+)$\", result.stdout)",
  "    require(len(hits) == 1, \"Наблюдатель архива не завершил измерение\")",
  "    return int(hits[0])",
  "",
  "",
  "def expect_command(result, expected=True, archive_expected=False):",
  "    if expected:",
  "        require(result.returncode == 0, \"Ошибка процесса:\\n\" + result.stderr[-2000:])",
  "    require((archive_opens(result) > 0) == archive_expected, \"Неожиданное открытие архива\")",
  "    return result.stdout",
  "",
  "",
  "def http(lab, database, marker, *calls):",
  "    child = run_process(lab, database, marker, \"http\", (json.dumps(calls),))",
  "    output = expect_command(child)",
  "    results = [line.split(\"=\", 1)[1] for line in output.splitlines()",
  "               if line.startswith(\"HTTP_OBSERVATION=\")]",
  "    require(len(results) == 1, \"HTTP-наблюдение не получено\")",
  "    return json.loads(results[0])",
  "",
  "",
  "def sql_rows(database):",
  "    with closing(sqlite3.connect(database)) as connection:",
  "        return connection.execute(",
  "            \"SELECT id, title, priority, is_done, tags FROM tasks ORDER BY id\"",
  "        ).fetchall()",
  "",
  "",
  "def health_case(lab, database, marker):",
  "    print(\"Обычный адрес:\", \"sqlite:///\" + database.as_posix())",
  "    expect_command(run_process(lab, database, marker, \"init\"))",
  "    [[status, row]] = http(lab, database, marker, (\"POST\", \"/tasks\", {\"title\": TITLE, \"priority\": 3}))",
  "    require(status == 201 and row[\"title\"] == TITLE, \"API не создал синтетическую задачу\")",
  "    task_id = row[\"id\"]",
  "    command = f\"find\\n{task_id}\\ndone\\n{task_id}\\nexit\\n\"",
  "    cli = expect_command(run_process(lab, database, marker, \"cli\", typed=command))",
  "    require(f\"[{task_id}] {TITLE}\" in cli, \"CLI не увидел задачу из API\")",
  "    [[status, fresh]] = http(lab, database, marker, (\"GET\", f\"/tasks/{task_id}\", None))",
  "    require(status == 200 and fresh[\"is_done\"] is True, \"Новый API не увидел изменение CLI\")",
  "    require([row[:4] for row in sql_rows(database)] == [(task_id, TITLE, 3, 1)],",
  "            \"В указанной SQLite нет ожидаемой строки\")",
  "    require(not marker.exists(), \"Появился прежний файл\")",
  "    print(f\"Без старого файла: API и CLI видят id={task_id}, новый API видит is_done=True.\")",
  "",
  "",
  "def refusal_case(lab, marker):",
  "    marker.write_text(json.dumps([ARCHIVE_ROW], ensure_ascii=False), encoding=\"utf-8\")",
  "    before = marker.read_bytes()",
  "    unavailable = lab / \"no-parent\" / \"unavailable.sqlite3\"",
  "    statuses = http(lab, unavailable, marker, (\"GET\", \"/tasks\", None),",
  "                    (\"POST\", \"/tasks\", {\"title\": TITLE, \"priority\": 3}))",
  "    require(all(code >= 500 and ARCHIVE_ROW[\"title\"] not in str(body)",
  "                for code, body in statuses), \"API выдал ложный успех или архив\")",
  "    for typed in (\"list\\nexit\\n\", f\"add\\n{TITLE}\\n3\\nexit\\n\"):",
  "        command = run_process(lab, unavailable, marker, \"cli\", typed=typed)",
  "        expect_command(command, expected=False)",
  "        stopped = command.returncode != 0 or bool(",
  "            re.search(r\"остановлено|команда не выполнена\", command.stdout, re.IGNORECASE)",
  "        )",
  "        require(stopped and not re.search(r\"\\[\\d+\\]|Список задач пуст\\.\", command.stdout)",
  "                and ARCHIVE_ROW[\"title\"] not in command.stdout, \"CLI выдал ложный результат\")",
  "    require(marker.read_bytes() == before and not unavailable.exists(), \"Исходные данные изменились\")",
  "    print(\"Отказ: API и CLI остановились, открытий архива 0, байты совпадают.\")",
  "",
  "",
  "def explicit_case(lab, marker):",
  "    target = lab / \"import.sqlite3\"",
  "    before = marker.read_bytes()",
  "    expect_command(run_process(lab, target, marker, \"init\"))",
  "    require(sql_rows(target) == [], \"init автоматически наполнил таблицу\")",
  "    [[status, result]] = http(lab, target, marker, (\"GET\", \"/tasks\", None))",
  "    cli = expect_command(run_process(lab, target, marker, \"cli\", typed=\"list\\nexit\\n\"))",
  "    require(status == 200 and result == [] and \"Список задач пуст.\" in cli",
  "            and sql_rows(target) == [], \"Обычный запуск перенёс данные без команды\")",
  "    args = (\"--source\", str(marker), \"--database\", str(target))",
  "    expect_command(run_process(lab, target, marker, \"import\", args), archive_expected=True)",
  "    rows = sql_rows(target)",
  "    require(len(rows) == 1 and rows[0][:4] == (41, ARCHIVE_ROW[\"title\"], 2, 0)",
  "            and json.loads(rows[0][4]) == [\"архив\"], \"Явный перенос дал другую строку\")",
  "    repeated = run_process(lab, target, marker, \"import\", args)",
  "    require(repeated.returncode != 0 and sql_rows(target) == rows and marker.read_bytes() == before,",
  "            \"Повторный импорт изменил цель или архив\")",
  "    print(\"Импорт: 0 строк после init и обычного запуска, 1 после явной команды.\")",
  "    return target",
  "",
  "",
  "def regress(lab, target, marker):",
  "    calls = (",
  "        (\"PUT\", \"/tasks/41\", {\"title\": \"Сверить склад\", \"priority\": 4, \"is_done\": True}),",
  "        (\"PATCH\", \"/tasks/41\", {\"is_done\": False}),",
  "        (\"PATCH\", \"/tasks/41\", {}),",
  "        (\"GET\", \"/tasks?is_done=false&limit=1\", None),",
  "        (\"GET\", \"/stats\", None),",
  "        (\"POST\", \"/tasks\", {\"title\": \"Без приоритета\"}),",
  "        (\"GET\", \"/tasks/9999\", None),",
  "        (\"PUT\", \"/preferences\", {\"view\": \"detailed\"}),",
  "        (\"GET\", \"/preferences\", None),",
  "        (\"DELETE\", \"/preferences\", None),",
  "    )",
  "    replies = http(lab, target, marker, *calls)",
  "    require([pair[0] for pair in replies] == [200, 200, 200, 200, 200, 422, 404, 200, 200, 204],",
  "            f\"Изменились статусы API: {[pair[0] for pair in replies]}\")",
  "    require(replies[2][1][\"is_done\"] is False and replies[3][1][0][\"id\"] == 41",
  "            and replies[4][1] == {\"total\": 1, \"open\": 1, \"done\": 0}",
  "            and replies[8][1][\"view\"] == \"detailed\"",
  "            and json.loads(sql_rows(target)[0][4]) == [\"архив\"], \"Изменился прежний договор данных\")",
  "    cli = expect_command(run_process(lab, target, marker, \"cli\", typed=\"search\\nСклад\\nstats\\nexit\\n\"))",
  "    require(\"[41] Сверить склад\" in cli, \"Поиск CLI потерял запись\")",
  "    replies = http(lab, target, marker, (\"DELETE\", \"/tasks/41\", None), (\"GET\", \"/tasks/41\", None))",
  "    require([pair[0] for pair in replies] == [204, 404], \"Удаление изменило договор\")",
  "    print(\"Регрессия обычной сборки: CRUD, теги, выборка, поиск, статистика, preferences, 404/422.\")",
  "",
  "",
  "def verify(mode):",
  "    outline()",
  "    if mode == \"check\":",
  "        for path in (ROOT / \"app\").rglob(\"*.py\"):",
  "            require(not LEGACY.search(source(path)), f\"В карте остался старый путь: {path}\")",
  "    with TemporaryDirectory(prefix=\"planner-sql-only-\") as folder:",
  "        lab = Path(folder)",
  "        shutil.copytree(ROOT / \"app\", lab / \"app\",",
  "                        ignore=shutil.ignore_patterns(\"__pycache__\", \"*.pyc\"))",
  "        marker = lab / \"data\" / \"tasks.json\"",
  "        marker.parent.mkdir()",
  "        health_case(lab, lab / \"working.sqlite3\", marker)",
  "        if mode == \"check\":",
  "            refusal_case(lab, marker)",
  "            target = explicit_case(lab, marker)",
  "            regress(lab, target, marker)",
  "            print(\"Изолированные проверки прошли. Отдельно запустите python -m pytest -q.\")",
  "",
  "",
  "def main():",
  "    parser = ArgumentParser(description=\"Открытая локальная проверка SQL-сборки Planner\")",
  "    parser.add_argument(\"mode\", choices=(\"baseline\", \"cleanup\", \"check\"))",
  "    mode = parser.parse_args().mode",
  "    try:",
  "        require((ROOT / \"app\" / \"main.py\").is_file() and (ROOT / \"tests\").is_dir(),",
  "                \"Запустите скрипт из project/, рядом с app/ и tests/\")",
  "        if mode == \"cleanup\":",
  "            cleanup()",
  "        else:",
  "            verify(mode)",
  "    except (RuntimeError, OSError, SyntaxError, subprocess.TimeoutExpired) as error:",
  "        print(\"Стоп:\", error, file=sys.stderr)",
  "        raise SystemExit(1) from error",
  "",
  "",
  "if __name__ == \"__main__\":",
  "    main()",
].join("\n");

type Outcome = "projector" | "stopped" | "imported";

const expected: Record<Outcome, string> = {
  projector: "В SQLite найдётся Проектор",
  stopped: "Операция остановится",
  imported: "В отдельной цели появится Радиомикрофон",
};

const controlStyle = { minHeight: "2.5rem", maxWidth: "100%", font: "inherit" };

function CatalogTrial() {
  const [available, setAvailable] = useState(true);
  const [archive, setArchive] = useState(false);
  const [explicit, setExplicit] = useState(false);
  const [prediction, setPrediction] = useState<Outcome | "">("");
  const [checked, setChecked] = useState(false);
  const outcome: Outcome = !available || (explicit && !archive)
    ? "stopped"
    : explicit ? "imported" : "projector";
  const reason = !available
    ? "Выбранную SQLite открыть нельзя. Операция не переключается на другой источник."
    : explicit
      ? archive
        ? "Явная команда прочитала архив и записала данные в отдельную пустую цель."
        : "Явной команде нужен файл снимка. Сейчас его нет."
      : "Каталог прочитал рабочую SQLite. Состояние архива на результат не влияет.";

  function reset() {
    setAvailable(true);
    setArchive(false);
    setExplicit(false);
    setPrediction("");
    setChecked(false);
  }

  return (
    <section aria-label="Проба клубного каталога" style={{
      width: "100%", boxSizing: "border-box", padding: "1rem",
      border: "1px solid currentColor", borderRadius: "0.75rem",
    }}>
      <p><strong>Предскажем результат</strong></p>
      <p>В рабочей SQLite есть №4 «Проектор». В архиве есть №8 «Радиомикрофон». Цель для импорта изначально пуста.</p>
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 13rem), 1fr))",
        gap: "0.75rem", marginBlock: "0.75rem",
      }}>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Доступ к SQLite
          <select style={controlStyle} value={available ? "ready" : "missing"} onChange={(event) => {
            setAvailable(event.target.value === "ready");
            setChecked(false);
          }}>
            <option value="ready">Файл доступен</option>
            <option value="missing">Папка отсутствует</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Архив
          <select style={controlStyle} value={archive ? "present" : "hidden"} onChange={(event) => {
            setArchive(event.target.value === "present");
            setChecked(false);
          }}>
            <option value="hidden">Файл убран</option>
            <option value="present">Файл на месте</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Действие
          <select style={controlStyle} value={explicit ? "import" : "read"} onChange={(event) => {
            setExplicit(event.target.value === "import");
            setChecked(false);
          }}>
            <option value="read">Обычное чтение</option>
            <option value="import">Явный импорт</option>
          </select>
        </label>
      </div>
      <label style={{ display: "grid", gap: "0.25rem", maxWidth: "25rem" }}>
        Что увидим?
        <select style={{ ...controlStyle, width: "100%", minWidth: 0 }} value={prediction} onChange={(event) => {
          setPrediction(event.target.value as Outcome | "");
          setChecked(false);
        }}>
          <option value="">Сделаем прогноз</option>
          {Object.entries(expected).map(([value, text]) => (
            <option key={value} value={value}>{text}</option>
          ))}
        </select>
      </label>
      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginBlockStart: "0.75rem" }}>
        <button style={controlStyle} type="button" disabled={!prediction} onClick={() => setChecked(true)}>Проверить</button>
        <button style={controlStyle} type="button" onClick={reset}>Сбросить</button>
      </div>
      <div aria-live="polite">
        {checked && (
          <p style={{ marginBlockStart: "0.75rem", marginBlockEnd: 0 }}>
            <strong>{prediction === outcome ? "Прогноз совпал." : "Получилось иначе."}</strong>{" "}
            {expected[outcome]}. {reason}<br />
            {explicit ? "Явный импорт → снимок и пустая цель." : "Обычный вход → сервис → SQLite."}{" "}
            Открытий архива: {explicit && available && archive ? 1 : 0}.
          </p>
        )}
      </div>
    </section>
  );
}

export function Lesson75_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Блок 15 · Полный SQL-путь и единое переключение"}
        title="Убираем файловый backend из рабочей сборки"
        intro="Оставим Planner один рабочий источник и проверим, что старый файл используется только для явного импорта."
      />

      <Section number="00" title="После общего перехода">
        <LinkedNotes items={[
          { title: "Точка старта", description: "API и CLI работают с одной SQLite. Мы сверили перенесённые задачи и сохранили архив." },
          { title: "Новый вопрос", description: "Как убрать старый путь из программы и проверить, что она снова не выберет файл?" },
          { title: "Результат", description: "Planner работает без старого файла. Если SQLite недоступна, операция останавливается, а архив остаётся цел." },
        ]} />
        <p>Переход уже показал, что новое изменение переживает перезапуск. Теперь проследим, откуда программа берёт хранилище. Архив оставим для отдельной команды переноса.</p>
      </Section>

      <Section number="01" title="Кто выбирает хранилище">
        <Lead>Сервис выполняет операцию. Репозиторий читает и записывает данные. Сборка связывает их и даёт сервису нужный репозиторий на время запроса или команды.</Lead>
        <p>В клубном каталоге экран и команда чтения уже обращаются к SQLite. В сборке ещё есть прежний вариант. Если репозиторий не передали, программа выбирает адаптер файла. Даже редкая ветка требует, чтобы импортированный модуль был на месте.</p>
        <Diagram caption="Обычная сборка каталога и отдельная команда переноса" orientation="vertical">
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 16rem), 1fr))",
            gap: "0.75rem",
          }}>
            <DiagramLane title="До уборки">
              <DiagramFlow label="Переходная сборка" orientation="vertical">
                <DiagramNode title="Экран и команда чтения" />
                <DiagramArrow />
                <DiagramNode title="Сборка каталога">Обычный вызов передаёт SQL-репозиторий. Вызов без него выбирает адаптер файла.</DiagramNode>
              </DiagramFlow>
            </DiagramLane>
            <DiagramLane title="После уборки">
              <DiagramFlow label="Обычная сборка" orientation="vertical">
                <DiagramNode title="Экран и команда чтения" />
                <DiagramArrow />
                <DiagramNode title="Сервис каталога → SQLite">Оба входа передают SQL-репозиторий.</DiagramNode>
              </DiagramFlow>
            </DiagramLane>
            <DiagramLane title="Отдельная команда">
              <DiagramFlow label="Перенос из архива" orientation="vertical">
                <DiagramNode title="Явный импорт" />
                <DiagramArrow />
                <DiagramNode title="Выбранный архив → пустая SQLite">Утилита сама открывает снимок по переданному пути.</DiagramNode>
              </DiagramFlow>
            </DiagramLane>
          </div>
        </Diagram>
        <p>Какой потребитель ещё удерживает старый модуль? Тест, который импортирует адаптер файла, всё ещё нуждается в нём. Импортёр читает снимок напрямую, поэтому его отдельный путь сохраняется. JSON в ответе API или поле <code>tags</code> описывает формат данных и работает независимо от старого объекта хранения.</p>
        <p>В Planner начинаем карту с обычных API и CLI и идём к сервису. Отмечаем, кто создаёт репозиторий, кто передаёт его и где ещё доступен файловый вариант. Каждый запрос или команда получает короткую Session. Область записи подтверждает изменение до завершения операции.</p>
      </Section>

      <Section number="02" title="Проверяем выбранный адрес">
        <p>Автоматический переход на запасной источник при отказе называют fallback. Чтобы его заметить, сравним обычную работу без файла с отказом базы, когда старый файл рядом и содержит отличимую запись.</p>
        <p>В каталоге обычная операция открывает выбранную SQLite. В <code>club.sqlite3</code> есть №4 «Проектор». Архив содержит №8 «Радиомикрофон». Для отказа выбираем путь внутри отсутствующей папки. Для импорта используем другой файл <code>import.sqlite3</code> с пустой таблицей.</p>
        <details>
          <summary>Готовый пример club_catalog_demo.py</summary>
          <p>Можно прочитать наблюдения ниже или сохранить весь блок как <code>club_catalog_demo.py</code> и запустить <code>python club_catalog_demo.py</code>. Достаточно обычного Python. Программа сама подготовит временные данные.</p>
          <CodeBlock code={catalogDemo} caption="club_catalog_demo.py" />
        </details>
        <CodeBlock caption="Вывод примера" code={"Обычное чтение без архива: [(4, 'Проектор')]\nЧтение: операция остановилась\nЗапись: операция остановилась\nБайты архива совпадают: True\nПустая цель после init и обычного чтения: []\nПосле явного импорта: [(8, 'Радиомикрофон')]"} />
        <p><code>init_catalog</code> готовит таблицу. Обычные чтение и запись получают адрес SQLite и останавливаются, если его нельзя открыть. <code>import_archive</code> получает сразу два явных входа, снимок и цель. Именно этот вызов переносит данные.</p>
        <CatalogTrial />
        <p>Равные байты говорят, что файл сохранился. Чтение байты не меняет, поэтому в практике наблюдатель считает открытия контрольного пути. Новый процесс получает временный адрес до запуска. Это позволяет проверить выбранный источник вместо уже открытого соединения.</p>
      </Section>

      <Section number="03" title="Готовая проверка для Planner">
        <Lead>Сами уберём файловую связь в сборке. Повторяющиеся изменения прежних фикстур и настроек возьмём из готового файла.</Lead>
        <p>Фикстура даёт тесту подготовленные данные или ресурс. Тест «адаптер записывает снимок» относится к прежнему модулю. Сценарий «обычная запись видна при новом чтении» остаётся важным. В готовой проверке он обращается к SQLite через обычные точки.</p>
        <p>Скопируем весь <code>check_sql_only.py</code> в <code>project/</code> рядом с <code>app/</code> и <code>tests/</code>. Если имя занято, используем <code>check_sql_only_local.py</code> во всех командах. Запускаем привычным Python из <code>project/</code>. Для HTTP-проверки нужны тестовые зависимости из <code>requirements-dev.txt</code>.</p>
        <CodeBlock caption="Команды из корня Planner" code={"python check_sql_only.py baseline\npython check_sql_only.py cleanup\npython check_sql_only.py check\npython -m pytest -q"} />
        <ul>
          <li><strong>baseline</strong> читает карту, показывает один временный адрес и новый id в обоих интерфейсах. Сверяем результат со своим <code>result.md</code> о переключении.</li>
          <li><strong>cleanup</strong> применяем после собственной правки. Команда сохраняет исходники и переносит активные проверки на временную SQLite. Среди них зависимость API от Session, публичная карточка без внутренних тегов, сохранение и сброс настроек в разных клиентах и команды CLI. Проверки старого файлового адаптера остаются в архиве.</li>
          <li><strong>check</strong> показывает работу без файла, отказ с контрольным JSON и появление строки только после явного импорта.</li>
          <li><strong>pytest</strong> запускаем отдельно из того же каталога. Он соберёт весь набор проекта, в том числе сохранённые проверки. Если у вас есть свои тесты, перед запуском убедитесь, что они используют временную базу. Если есть падение, итог ещё не готов.</li>
        </ul>
        <p>Если команда выводит «Стоп», читаем, какую цепочку, файл или строку отчёта она указала. Если шаблон не узнаёт старую конструкцию, он остановится до изменения файлов. Рабочая база и архив остаются на своих местах.</p>
        <details>
          <summary>Скопировать готовый файл check_sql_only.py</summary>
          <CodeBlock code={sqlOnlyCheck} caption="check_sql_only.py" />
        </details>
        <details>
          <summary>Подготовить раздел запуска в README</summary>
          <p>Сохраните свою инструкцию установки зависимостей и примеры запросов. Замените описание хранилища и команды запуска в README. API и CLI работают с одной SQLite. В примере используется локальная база <code>data/planner.sqlite3</code>. В корне <code>project/</code> с установленными зависимостями готовим папку для файла, создаём исходную схему и запускаем CLI.</p>
          <CodeBlock language="text" caption="Терминал CLI, PowerShell" code={'$env:DATABASE_URL = "sqlite:///data/planner.sqlite3"\nNew-Item -ItemType Directory -Force data | Out-Null\npython -m app.DBase.init_db\npython -m app.main'} />
          <p>Для API в другом терминале указываем тот же адрес и запускаем сервер из той же папки. Чтение <code>GET /tasks</code> показывает задачи из этого файла. Если в README остались инструкции по JSON, оставьте их в отдельном разделе об импорте.</p>
          <CodeBlock language="text" caption="Терминал API, PowerShell" code={'$env:DATABASE_URL = "sqlite:///data/planner.sqlite3"\npython -m uvicorn app.api:app --host 127.0.0.1 --port 8000'} />
        </details>
        <Callout tone="success">Для следующих проверок пригодится режим check. Старые сценарии репетиции, которые обращаются к файловым настройкам Planner, сохраняем как историю перехода.</Callout>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Уберём лишний путь", description: "Проследим вызовы в своём Planner и уберём старую зависимость. API и CLI продолжат передавать SQL-репозиторий." },
          { title: "Проверим запуск и остановку", description: "Увидим одну запись из нового процесса без JSON, а при недоступной SQLite получим отказ и ноль открытий контрольного файла." },
          { title: "Проверим импорт и сохраним запуск", description: "Увидим, что контрольная строка появляется только после импорта. Прогоним все тесты и перенесём в README команды запуска с общей SQLite." },
        ]} />
        <p>Если карта расходится с ожиданием, вернёмся к связям между входом, сборкой и репозиторием. Если расходится отчёт, сравним исправный адрес, отказ и пустую цель с клубным примером.</p>
      </Section>
    </RichLesson>
  );
}
