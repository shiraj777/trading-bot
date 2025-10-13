IMAGE=trading-bot
PORT=8000

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm -d -p $(PORT):8000 --name $(IMAGE) $(IMAGE)

docker-stop:
	- docker rm -f $(IMAGE) || true

docker-test:
	curl -sSf http://127.0.0.1:$(PORT)/healthz | grep -q '"status":"ok"' && echo "✅ Health OK" || (echo "❌ Health check FAILED" && exit 1)