# Entry points for the public reproducibility package. `make help` lists them.
# Every target reads only config/ and examples/; outputs go to results/ or build/ (git-ignored).
PYTHON ?= python3
NET := blockchain/example-network

.PHONY: help install check test firmware-test go-test reproduce validate figures safety field-results \
        firmware network-up network-down integration benchmark-smoke failover-smoke clean

help:
	@echo "field-results   assemble plot-level results and run R analysis"
	@echo "install          pip install --require-hashes -r requirements-lock.txt"
	@echo "check            safety + firmware-test + test + go-test + reproduce (no Docker needed)"
	@echo "test             Python suite (also runs C++ host tests and chaincode/submitter Go tests)"
	@echo "firmware-test    C++ host tests, FRAME_MODE 15 and 3"
	@echo "go-test          gofmt, vet and tests of the three Go modules"
	@echo "reproduce        analytical results + validation -> results/analytical/"
	@echo "safety           public-data safety checks (run before every commit)"
	@echo "firmware         ESP32 build with PlatformIO 6.1.16 -> build/firmware/node/"
	@echo "network-up       example Fabric 2.5.16 network: up, channel, deploy, identities"
	@echo "integration      eight-step submitter test against the running example network"
	@echo "benchmark-smoke  one-level RBAC comparison (example-network-run results)"
	@echo "failover-smoke   one recovery + one split-vote Raft trial (example-network-run results)"
	@echo "network-down     remove containers, volumes and all generated crypto"
	@echo "clean            remove ./results, ./build and Python caches"

install:
	$(PYTHON) -m pip install --require-hashes -r requirements-lock.txt

check: safety firmware-test test go-test reproduce

field-results:
	@test -n "$(FIELD_DATA)" || (echo "Set FIELD_DATA to your external agronomic CSV directory"; exit 2)
	$(PYTHON) scripts/prepare_field_plots.py --data-dir "$(FIELD_DATA)"
	$(PYTHON) scripts/reconcile_field_water.py --data-dir "$(FIELD_DATA)"
	Rscript analysis/agronomic_statistics.R --plots results/manuscript/field_plot_inputs.csv --out results/manuscript/agronomic

test:
	$(PYTHON) -m pytest -q tests

firmware-test:
	$(MAKE) -C firmware/esp32/tests
	$(MAKE) -s -C firmware/esp32/tests clean

go-test:
	@for m in blockchain/chaincode/hrbac-cc gateway/fabric-submitter benchmarks/loadgen; do \
	  echo "== $$m"; (cd $$m && test -z "$$(gofmt -l .)" && go vet ./... && go test -count=1 ./...) || exit 1; done

reproduce:
	$(PYTHON) analysis/reproduce_analytical_results.py --out results/analytical
	$(PYTHON) analysis/validate_analytical_outputs.py --results results/analytical/analytical_results.json

validate:
	$(PYTHON) analysis/validate_analytical_outputs.py --results results/analytical/analytical_results.json

figures: reproduce

safety:
	$(PYTHON) -m pytest -q tests/test_public_data_safety.py

firmware:
	firmware/esp32/build_firmware.sh node

$(NET)/.env:
	cp $(NET)/.env.example $(NET)/.env
	sed -i "s/^CA_ADMIN_PASSWORD=.*/CA_ADMIN_PASSWORD=$$(openssl rand -hex 16)/" $(NET)/.env

network-up: $(NET)/.env
	$(NET)/network.sh up
	$(NET)/network.sh channel
	CC_PREBUILT=1 $(NET)/network.sh deploy
	$(NET)/network.sh identity
	$(NET)/network.sh gateway-identity ex-gw-north

integration:
	FABRIC_EXAMPLE_NETWORK=1 $(PYTHON) -m pytest -q tests/test_blockchain_integration.py

benchmark-smoke:
	PYTHON=$(PYTHON) WORKLOAD=examples/example_benchmark_config.yaml benchmarks/run_rbac_comparison.sh --campaign smoke-$$(date -u +%Y%m%dT%H%M%SZ)

failover-smoke:
	$(PYTHON) benchmarks/run_failover.py --campaign failover-smoke-$$(date -u +%Y%m%dT%H%M%SZ) --recovery 1 --split 1

network-down:
	$(NET)/network.sh down

clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('./results', './build', './.pytest_cache')]"
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(MAKE) -s -C firmware/esp32/tests clean
