<h1 align="center">
  <img src="docs/examples/logo.svg" alt="ai-quotas" width="64" valign="middle" /> ai-quotas
</h1>

<p align="center"><a href="README.md">English</a> | Русский</p>

<p align="center">
  Если хочешь поставить это себе — скопируй агенту:
</p>

<pre align="center">please set up https://github.com/calmmage/ai-quotas according to its instructions. Audit safety and guide me through the setup, bundle and automate everythin you can as a setup wizard script, logging what it does.</pre>

<p align="center">
  <strong>Остаток подписочной квоты — как она сгорает.</strong><br/>
  Claude, Codex, Grok и Gemini на одном дашборде — сэмплы с уже залогиненных CLI, хранятся на диске.<br/>
  <small>Gemini нужен отдельный адаптер; в публичном пакете его нет.</small>
</p>

<p align="center">
  <a href="https://github.com/calmmage/ai-quotas"><img src="https://img.shields.io/github/stars/calmmage/ai-quotas?style=flat&amp;label=%E2%98%85&amp;color=08C" alt="GitHub stars" /></a>
  <a href="https://github.com/calmmage/ai-quotas/actions/workflows/test.yml"><img src="https://github.com/calmmage/ai-quotas/actions/workflows/test.yml/badge.svg" alt="test" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-08C?style=flat" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/runtime-stdlib-6e7681?style=flat" alt="stdlib runtime" />
  <img src="https://img.shields.io/badge/macOS-111318?style=flat" alt="macOS" />
</p>

<p align="center">
  <a href="https://code.claude.com/docs"><kbd>Claude</kbd></a>
  &nbsp;
  <a href="https://github.com/openai/codex"><kbd>Codex</kbd></a>
  &nbsp;
  <a href="https://x.ai/cli"><kbd>Grok</kbd></a>
  &nbsp;
  <a href="https://gemini.google.com"><kbd>Gemini</kbd></a>
</p>

<h3 align="center"><a href="#установка"><ins>Установить ai-quotas</ins></a></h3>

<p align="center">
  <a href="docs/examples/dash-night.png"><img src="docs/examples/dash-night.png" alt="ночной дашборд ai-quotas: оставшийся % для Claude, Codex, Grok и Gemini" width="960" /></a>
</p>

> Эндпоинты usage у вендоров неофициальные и могут измениться. Инструмент читает **твои** креды, только на чтение. На свой страх и риск.

## Установка

Нужны **Python ≥ 3.11**, **[uv](https://docs.astral.sh/uv/)** и **make**.

```bash
git clone https://github.com/calmmage/ai-quotas.git
cd ai-quotas
make setup                 # uv sync --extra all + doctor
```

Ядро — **только stdlib**; `make setup` ещё ставит зависимости графиков. Сэмплы остаются на этой машине (по умолчанию `~/.local/share/ai-quotas/`). Отдельный аккаунт ai-quotas не нужен: живые данные идут из уже существующих логинов вендоров.

**Агентам:** установка, логины и опциональная автоматизация — в **[AGENTS.md](AGENTS.md)**.

### Открыть дашборд

Залогинься в CLI вендоров, которыми пользуешься, затем:

```bash
make sample                # один сбор с залогиненных CLI
make dash                  # сгенерировать и открыть локальный дашборд
```

Дашборд открывается на `http://127.0.0.1:8765/live.html`. Оставь процесс, чтобы видеть новые сэмплы; `make sample` собирает их снова. Опциональный сбор по расписанию — в [AGENTS.md](AGENTS.md).

Таблица офлайн, без аккаунтов вендоров:

```bash
AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh
```

## Что получается

| Поверхность | Команда |
|---------|---------|
| **Таблица** | `uv run ai-quotas` — used% · burn vs need · reset ETA · цвет темпа |
| **Даш** | `make dash` — % remaining во времени, денежные маркеры, бейджи reset-credit |
| **Вердикты** | `uv run ai-quotas verdicts` — `STOP` / `WARN` / `OK` (код выхода 2 / 1 / 0) |
| **Алерты** | Telegram, когда **сжигается** ещё высокий бар, или **reset скоро**, а квота осталась |
| **Spend** | `uv run ai-quotas spend` — локальные токены/$ сессий (логи Claude / Codex / Grok) |
| **Автоматизация** | `make install-automation` — сэмпл каждые 30м + dash KeepAlive + недельная проверка spend |

Дефолтный 2×2 — **Claude / Codex / Grok / Gemini**. Gemini — внешний адаптер (`AI_QUOTAS_EXTRA_ADAPTERS`). OpenRouter встроенный, показывается с `--full`, не на этом 2×2.

## Отдать агенту

Вставь:

> Clone https://github.com/calmmage/ai-quotas and follow **[AGENTS.md](AGENTS.md)**. Install [uv](https://docs.astral.sh/uv/) if missing. Do not invent an install path. Vendor CLI logins are the human's. Skip Gemini unless they give you an extra adapter (`AI_QUOTAS_EXTRA_ADAPTERS`). Then `make sample && make dash`.

Гайд также покрывает опциональные macOS LaunchAgents, Telegram-алерты и пинги Healthchecks.

## Документация

| | |
|---|---|
| Установка / деплой / интеграция для агентов | [AGENTS.md](AGENTS.md) |
| Движки графиков, деньги, reset credits | [docs/PLOTS.md](docs/PLOTS.md) |
| Контракт строк сэмпла + вердикты | [docs/CONTRACT.md](docs/CONTRACT.md) |
| Подписи leftover-токенов | [docs/TOKEN-GAUGE.md](docs/TOKEN-GAUGE.md) |
| Сообщения об уязвимостях | [SECURITY.md](SECURITY.md) |

```bash
make test     # офлайн
make doctor   # пути
make grok-fix # починить Grok-аутентификацию
```

## Лицензия

MIT — см. [LICENSE](LICENSE). Уязвимости: [SECURITY.md](SECURITY.md).
