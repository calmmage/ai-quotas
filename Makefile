# ai-quotas — sample / table / plots / automation
# Keep core stdlib-only; plot stack is optional: `make install-plot`

.PHONY: help install install-plot install-all test sample table plot dash money setup \
	install-automation uninstall-automation dry-run-automation doctor clean-plots

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
	@echo "  make install          # core only (stdlib runtime)"
	@echo "  make install-plot     # + pandas/matplotlib/plotly"
	@echo "  make install-all      # dev + plot"
	@echo "  make test             # pytest"
	@echo "  make sample           # probe + append samples.jsonl"
	@echo "  make table            # human table (--no-refresh)"
	@echo "  make plot             # generate dashboards (needs install-plot)"
	@echo "  make dash             # generate + serve 127.0.0.1 (regen on mtime)"
	@echo "  make money            # plot + money report"
	@echo "  make setup            # install-all + doctor + sample dry path"
	@echo "  make install-automation  # LaunchAgent → ai-quotas sample (macOS)"
	@echo "  make dry-run-automation  # print resolved program path"
	@echo "  make doctor           # show paths / versions"
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

setup: install-all doctor
	@echo ""
	@echo "Next:"
	@echo "  make sample          # collect once"
	@echo "  make table           # view table"
	@echo "  make plot            # write dashboards"
	@echo "  make dash            # serve dashboards locally"
	@echo "  make install-automation   # optional LaunchAgent (macOS)"

doctor:
	@$(PYTHON) -c "from ai_quotas import __version__, samples_path; from ai_quotas.paths import data_dir, plots_dir; \
print('ai-quotas', __version__); \
print('samples ', samples_path()); \
print('data_dir', data_dir()); \
print('plots   ', plots_dir())"
	@$(UV) run ai-quotas --help >/dev/null && echo "cli: ok"

dry-run-automation:
	@bash scripts/install-launchagent.sh --dry-run --interval $(INTERVAL)

install-automation:
	@bash scripts/install-launchagent.sh --interval $(INTERVAL)

uninstall-automation:
	@bash scripts/install-launchagent.sh --uninstall

clean-plots:
	@rm -rf .plots-out
	@echo "removed ./.plots-out (if any); default runtime dir is \$$AI_QUOTAS_DATA_DIR/plots"
