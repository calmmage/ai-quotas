# ai-quotas — sample / table / plots / automation
# Keep core stdlib-only; plot stack is optional: `make install-plot`

.PHONY: help install install-plot install-all test sample table plot dash money setup \
	install-automation uninstall-automation dry-run-automation doctor clean-plots \
	agentic-step-check agentic-step-spend wizard alert

UV ?= uv
AI_QUOTAS ?= $(UV) run ai-quotas
PYTHON ?= $(UV) run python
SAMPLES ?=
PLOT_OUT ?=
INTERVAL ?= 1800
DASH_PORT ?= 8765
DASH_INTERVAL ?= 15

help:
	@echo "ai-quotas targets:"
	@echo "  make install          # uv sync --extra dev"
	@echo "  make install-plot     # uv sync --extra plot"
	@echo "  make install-all      # uv sync --extra all"
	@echo "  make test             # pytest"
	@echo "  make sample           # probe + append to SQLite"
	@echo "  make table            # human table (--no-refresh)"
	@echo "  make plot             # generate dashboards (needs install-plot)"
	@echo "  make dash             # generate + serve 127.0.0.1 (regen on DB change)"
	@echo "  make money            # plot + money report"
	@echo "  make setup            # uv sync --extra all + doctor"
	@echo "  make install-automation  # LaunchAgents: sample @30m + dash KeepAlive + weekly agentic_step"
	@echo "  make dry-run-automation  # print resolved program paths (no install)"
	@echo "  make wizard           # agent install: read AGENTS.md, then make setup"
	@echo "  make alert            # remaining/burn + reset-soon (dry-run)"
	@echo "  make agentic-step-check  # JSON verdict (exit 1 if substantial)"
	@echo "  make agentic-step-spend  # join spend to agentic_step jobs"
	@echo "  make doctor           # show paths / version; verify CLI (cli: ok)"
	@echo "  make clean-plots      # remove local runtime plot dir if under ./"

install:
	$(UV) sync --extra dev

install-plot:
	$(UV) sync --extra plot

install-all:
	$(UV) sync --extra all

test:
	$(UV) run pytest

# Root flags (--samples) must come *before* the subcommand name.
sample:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) sample

table:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) --no-refresh

plot:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) plot $(if $(PLOT_OUT),--out $(PLOT_OUT),)

dash:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) dash \
		$(if $(PLOT_OUT),--out $(PLOT_OUT),) \
		--port $(DASH_PORT) --interval $(DASH_INTERVAL) --open

money:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) plot --money $(if $(PLOT_OUT),--out $(PLOT_OUT),)

setup: install-all
	@$(MAKE) doctor
	@echo ""
	@echo "Next:"
	@echo "  # offline proof, no vendor accounts:"
	@echo "  AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh"
	@echo "  make sample          # collect once"
	@echo "  make table           # view table"
	@echo "  make plot            # write dashboards"
	@echo "  make dash            # serve dashboards locally"
	@echo "  make install-automation    # optional LaunchAgents (macOS): sample + dash + weekly check"
	@echo "  (agents: read AGENTS.md — make wizard points there)"

doctor:
	@$(PYTHON) -c "from ai_quotas import __version__; from ai_quotas.paths import doctor_report; \
print('ai-quotas', __version__); print(doctor_report())"
	@$(UV) run ai-quotas --help >/dev/null && echo "cli: ok"

agentic-step-spend:
	$(AI_QUOTAS) spend --agentic-step --no-harvest --since 7d

agentic-step-check:
	$(AI_QUOTAS) agentic-step-check --since 7d

dry-run-automation:
	@bash scripts/install-launchagent.sh --dry-run --interval $(INTERVAL)
	@bash scripts/install-dash.sh --dry-run --port $(DASH_PORT) --interval $(DASH_INTERVAL)
	@bash scripts/install-agentic-step-alert.sh --dry-run

install-automation:
	@bash scripts/install-launchagent.sh --interval $(INTERVAL)
	@bash scripts/install-dash.sh --port $(DASH_PORT) --interval $(DASH_INTERVAL)
	@bash scripts/install-agentic-step-alert.sh

uninstall-automation:
	@bash scripts/install-launchagent.sh --uninstall
	@bash scripts/install-dash.sh --uninstall
	@bash scripts/install-agentic-step-alert.sh --uninstall

wizard:
	@echo "Agent install wizard — the steps live in AGENTS.md."
	@echo "  1. open AGENTS.md"
	@echo "  2. this target runs: make setup"
	@echo ""
	@$(MAKE) setup
	@echo ""
	@echo "Done when: \`make doctor\` prints \`cli: ok\`."
	@echo "Offline (no vendor accounts):"
	@echo "  AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh"
	@echo "Next (human): log into vendor CLIs (claude / grok / codex), then:"
	@echo "  make sample && make table && make dash"
	@echo "  make install-automation   # optional, macOS"

alert:
	$(AI_QUOTAS) $(if $(SAMPLES),--samples $(SAMPLES),) alert --dry-run

clean-plots:
	@rm -rf .plots-out
	@echo "removed ./.plots-out (if any); default runtime dir is \$$AI_QUOTAS_DATA_DIR/plots"
