.PHONY: audit benchmark demo eval quality test

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

quality:
	python3 scripts/quality.py

audit:
	PYTHONPATH=src python3 -m agentcompat audit \
		--schema examples/order-api/baseline.json
	PYTHONPATH=src python3 -m agentcompat audit \
		--schema examples/order-api/candidate.json

demo:
	PYTHONPATH=src python3 -m agentcompat check \
		--baseline examples/order-api/baseline.json \
		--candidate examples/order-api/candidate.json \
		--traces examples/order-api/traces.jsonl \
		--fail-under 50

eval:
	PYTHONPATH=src python3 -m agentcompat eval \
		--suite examples/order-api/suite.json

benchmark:
	PYTHONPATH=src python3 -m agentcompat benchmark \
		--calls 1000000 \
		--sample-size 10000 \
		--sample-seed 17 \
		--score-tolerance 2 \
		--max-memory-mib 512
