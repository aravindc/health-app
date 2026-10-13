# Development commands. Run `make help` for a list.
# WSL: the GPU driver library lives in /usr/lib/wsl/lib, which the loader cache may not include.
# Do not set OMP_WAIT_POLICY=passive: it makes LightGBM ~6x slower here (measured 62 s vs 11 s per fit).
export LD_LIBRARY_PATH := /usr/lib/wsl/lib:$(LD_LIBRARY_PATH)
.PHONY: help install ingest ingest-full validate features cv cv-seq evaluate final holdout analyze phase1 phase2 phase3 phase4 phase5 api docker-api models experiments update update-check roll daily monitor refresh refresh-compare lows pretrain tune dashboard test lint clean-db

help:          ## list commands
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:       ## create .venv and install dependencies
	uv sync --group dev --extra pipeline --extra ml --extra dl --extra api --extra dashboard

ingest:        ## copy new rows from Postgres and rebuild clean tables
	uv run python -m src.ingest

ingest-full:   ## re-copy all rows from Postgres
	uv run python -m src.ingest --full

validate:      ## data-quality report + figures in reports/
	uv run python -m src.validate_data

phase1: ingest validate  ## ingest then report

features:      ## grid, features, targets, splits + leakage audit -> reports/feature_report.md
	uv run python -m src.features --audit 80

phase2: phase1 features  ## everything up to features

cv:            ## rolling-origin cross-validation of all models (~45 min)
	uv run python -m src.train --cv

evaluate:      ## model comparison report -> reports/model_comparison.md
	uv run python -m src.evaluate

phase3: cv evaluate  ## baselines + tree models + comparison report

cv-seq:        ## rolling-origin cross-validation of the LSTM and TCN (GPU if available)
	uv run python -m src.train --cv --experiment sequence

phase4: cv-seq evaluate  ## sequence models + updated comparison report

final:         ## fit the selected model (random forest, delta) on all development data -> models/
	uv run python -m src.train --final --model random_forest --target-type delta

holdout:       ## ONE-TIME test-period evaluation (refuses to run twice)
	uv run python -m src.holdout

analyze:       ## intervals, SHAP, error analysis, events, examples -> reports/test_report.md (~16 min)
	uv run python -m src.analyze

phase5: analyze  ## Phase 5 analysis (needs `make final holdout` once)

lows:          ## "low within 30 min?" comparison on validation months -> reports/lows_report.md (~16 min)
	uv run python -m src.lows

pretrain:      ## older Nightscout glucose history experiment -> reports/pretraining_report.md (~16 min)
	uv run python -m src.pretrain

tune:          ## nested hyperparameter tuning (random forest ~75 min, LightGBM ~10, TCN ~55 on GPU)
	uv run python -m src.tune

dashboard:     ## interactive offline dashboard -> reports/dashboard.html (also at /dashboard in the API)
	uv run python -m src.dashboard

api:           ## run the prediction API on http://127.0.0.1:8000 (docs at /docs)
	uv run uvicorn src.api:app --host 127.0.0.1 --port 8000

docker-api:    ## run the prediction API in Docker (health-app's compose: http://health-predict:8000 on health_be)
	cd .. && docker compose up -d --build health-predict

models:        ## list model versions and which are in production
	uv run python -m src.registry list

experiments:   ## show the experiment tracking table
	uv run python -m src.tracking show

daily:         ## sync + ingest new records, score yesterday, check drift, recalibrate intervals, reload API
	uv run python -m src.monitor --sync

monitor:       ## score/recalibrate on the records already ingested -> reports/monitor_report.md
	uv run python -m src.monitor

refresh:       ## monthly: refit the production recipe on all data up to yesterday -> shadow model
	uv run python -m src.refresh fit

refresh-compare: ## shadow vs production on the same live days (prints the promote command when ready)
	uv run python -m src.refresh compare

update:        ## full update cycle: sync, ingest, roll window, features, CV, candidate, score vs production (no promotion)
	uv run python -m src.update --holdout-weeks $(or $(WEEKS),8)

update-check:  ## sync + ingest, then report whether enough new data exists to roll the window
	uv run python -m src.update --check --holdout-weeks $(or $(WEEKS),8)

roll:          ## preview rolling the window forward (dry run)
	uv run python -m src.roll --holdout-weeks $(or $(WEEKS),8) --dry-run

test:          ## run tests
	uv run pytest

lint:          ## ruff
	uv run ruff check src tests

clean-db:      ## delete the DuckDB database (raw snapshot is re-copied on next ingest)
	rm -f data/processed/t1d.duckdb data/processed/t1d.duckdb.wal
