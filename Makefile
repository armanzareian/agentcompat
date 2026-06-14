.PHONY: demo eval quality test

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

quality:
	python3 scripts/quality.py

demo:
	PYTHONPATH=src python3 -m agentcompat check \
		--baseline examples/order-api/baseline.json \
		--candidate examples/order-api/candidate.json \
		--traces examples/order-api/traces.jsonl \
		--fail-under 50

eval:
	PYTHONPATH=src python3 -m agentcompat eval \
		--suite examples/order-api/suite.json
