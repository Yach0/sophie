PROJECT_DIR := "sophie_bot"

# Use uv for package management - no need for explicit environment path
PYTHON := "uv"
ASS_PATH := $(shell uv run python -c "import ass_tg as _; print(_.__path__[0])" 2>/dev/null)

# Export PYTHONPATH to support git worktrees
export PYTHONPATH := $(CURDIR)

# Use uv run for pybabel
PYBABEL := "pybabel"

NUITKA := "python" "-m" "nuitka"
NUITKA_ARGS := "--prefer-source-code" "--plugin-enable=pylint-warnings" "--follow-imports" \
			   "--include-package=sophie_bot" "--include-package-data=babel" "--assume-yes-for-downloads" "--lto=yes" \
			   "--python-flag=no_annotations" "--python-flag=isolated" "--product-name=SophieBot" \
			   "--output-dir=output/"
LOCALES_DIR := $(CURDIR)/locales


all: fix_code_style locale test_all clean build_onefile
commit: fix_code_style extract_lang test_code_style test_codeanalysis run_tests gen_wiki api migrate_status
test_all: test_code_style test_codeanalysis run_tests test_migrations
locale: extract_lang update_lang compile_lang


# Build

pull_libs:
	@echo "Pulling latest libs..."
	mkdir -p libs
	if [ ! -d "libs/stf" ]; then \
		git clone https://gitlab.com/SophieBot/stf.git libs/stf; \
	else \
		cd libs/stf && git pull; \
	fi
	if [ ! -d "libs/ass" ]; then \
		git clone https://gitlab.com/SophieBot/ass.git libs/ass; \
	else \
		cd libs/ass && git pull; \
	fi

sync_libs: pull_libs
	uv sync --reinstall-package ass-tg
	uv sync --reinstall-package stf-tg

clean:
	@echo "Cleaning build directories..."
	rm -rf output/

build_onefile:
	@echo "Building onefile..."
	uv run python -m nuitka $(PROJECT_DIR) $(NUITKA_ARGS) --standalone --onefile --linux-onefile-icon=build/icon.png

build_standalone:
	@echo "Building standalone..."
	uv run python -m nuitka $(PROJECT_DIR) $(NUITKA_ARGS) --standalone

# Development with hot-reload
dev_bot:
	@echo "Starting bot with hot-reload..."
	DEV_RELOAD=true MODE=bot uv run python -m sophie_bot

dev_rest:
	@echo "Starting REST API with hot-reload..."
	DEV_RELOAD=true MODE=rest uv run python -m sophie_bot

dev_scheduler:
	@echo "Starting scheduler with hot-reload..."
	DEV_RELOAD=true MODE=scheduler uv run python -m sophie_bot

fix_code_style:
	uv run python -m pycln . -a
	uv run ruff check . --fix
	uv run ruff format sophie_bot/

test_code_style:
	uv run python -m pycln . -a -c
	uv run ruff format sophie_bot/ --check
	uv run ruff check .

test_codeanalysis:
	# uv run python -m bandit sophie_bot/ -r
	uv run ty check

run_tests:
	uv run python -m pytest tests/ -v --alluredir=allure_results -n auto

# Locale

new_lang:
	$(PYBABEL) init -i "$(LOCALES_DIR)/sophie.pot" -d "$(LOCALES_DIR)" -D sophie -l "$(LANG)"

extract_lang:
	$(PYBABEL) extract -k "pl_:1,2" -k "p_:1,2" -k "l_:1" \
	--add-comments="NOTE: " -o "$(LOCALES_DIR)/bot.pot" --omit-header --sort-by-file --no-wrap $(PROJECT_DIR)

	cd "$(ASS_PATH)" && \
	$(PYBABEL) extract -k "pl_:1,2" -k "p_:1,2" -k "l_:1" \
	--add-comments="NOTE: " -o "$(LOCALES_DIR)/ass.pot" --omit-header --sort-by-file --no-wrap .

	# Merge
	cp "$(LOCALES_DIR)/bot.pot" "$(LOCALES_DIR)/sophie.pot"
	cat "$(LOCALES_DIR)/ass.pot" >> "$(LOCALES_DIR)/sophie.pot"

update_lang:
	$(PYBABEL) update -d "$(LOCALES_DIR)" -D "sophie" -i "$(LOCALES_DIR)/sophie.pot" \
	--ignore-pot-creation-date --omit-header --no-wrap

compile_lang:
	$(PYBABEL) compile -d "$(LOCALES_DIR)" -D "sophie" --use-fuzzy --statistics


new_locale:
	rm -rf locales/
	mkdir locales/

	make extract_lang
	make new_lang LANG=uk_UA
	make update_lang
	make compile_lang

# Wiki
gen_wiki:
	uv run python tools/wiki_gen/start.py


# REST API
gen_openapi:
	uv run python tools/openapi_gen/generate.py

 api:
	make gen_openapi
	if [ -d "../sdash" ]; then \
		cp openapi.json ../sdash/openapi.json; \
		cd ../sdash && bun run gen:api; \
	else \
		echo "Skipping sdash API generation: ../sdash directory not found"; \
	fi

# Database Migrations

new_migration:
	@if [ -z "$(NAME)" ]; then \
		echo "Error: NAME parameter is required. Usage: make new_migration NAME=add_new_field"; \
		exit 1; \
	fi
	@echo "Creating migration: $(NAME)"
	@uv run python tools/migration_helper.py create $(NAME)

migrate_up:
	@echo "Running migrations..."
	@uv run python tools/migration_helper.py up

migrate_status:
	@echo "Migration status:"
	@uv run python tools/migration_helper.py status

migrate_rollback:
	@if [ -z "$(MIGRATION)" ]; then \
		echo "Error: MIGRATION parameter is required. Usage: make migrate_rollback MIGRATION=20240125_001_add_field"; \
		exit 1; \
	fi
	@echo "Rolling back migration: $(MIGRATION)"
	@uv run python tools/migration_helper.py down $(MIGRATION)

migrate_down_all:
	@echo "Rolling back all migrations..."
	@uv run python tools/migration_helper.py down_all

test_migrations:
	@echo "Running migration tests..."
	@RUN_MIGRATIONS_ON_STARTUP=true MONGO_DB=sophie_test_migrations uv run python -m pytest tests/test_migrations.py -v

# Worktree support

setup_worktree:
	@echo "📦 Setting up worktree at: $(CURDIR)"
	@echo "🔄 Syncing dependencies..."

	@# Get the main repository directory
	@MAIN_GIT_DIR=$$(git rev-parse --git-common-dir 2>/dev/null || git rev-parse --git-dir); \
	MAIN_REPO_DIR=$$(cd "$$MAIN_GIT_DIR/.." && pwd); \
	\
	if [ ! -d "libs" ] && [ -d "$$MAIN_REPO_DIR/libs" ]; then \
		echo "  → Linking local libraries from main repo..."; \
		ln -s "$$MAIN_REPO_DIR/libs" libs; \
	fi; \
	\
	if [ ! -f "data/config.env" ] && [ -f "$$MAIN_REPO_DIR/data/config.env" ]; then \
		echo "  → Linking data directory from main repo..."; \
		rm -rf data; \
		ln -s "$$MAIN_REPO_DIR/data" data; \
	fi

	@echo "  → Running uv sync..."
	@uv sync --quiet

	@if [ -d "libs/stf" ] || [ -d "libs/ass" ]; then \
		echo "  → Syncing local libraries..."; \
		uv sync --reinstall-package ass-tg --quiet 2>/dev/null || true; \
		uv sync --reinstall-package stf-tg --quiet 2>/dev/null || true; \
	fi

	@if [ -d "locales" ]; then \
		echo "  → Compiling locale files..."; \
		uv run pybabel compile -d "locales" -D "sophie" --use-fuzzy 2>&1 | grep -E "(translated|compiling)" || true; \
	fi

	@if [ -f "Makefile" ] && ! grep -q "export PYTHONPATH" Makefile; then \
		echo "  → Patching Makefile for worktree support..."; \
		sed -i '/^ASS_PATH := /a\\n# Export PYTHONPATH to support git worktrees\\nexport PYTHONPATH := $$(CURDIR)' Makefile; \
	fi

	@echo "  → Installing package in editable mode..."
	@uv pip install -e . --quiet 2>/dev/null || true

	@echo "✅ Worktree setup complete!"

dev_branch: setup_worktree
	@echo ""
	@echo "🌲 Worktree is ready for development!"
	@echo "   Run 'make commit' to verify everything is working."
