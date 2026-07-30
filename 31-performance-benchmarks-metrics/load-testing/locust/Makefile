.PHONY: test validate package

IMAGE?=acmetocasino/load-testing
TAG?=latest

# locust stuff
ENV?=drill
WORKERS?=1
BETTING_PROVIDER_OFFERING_ID?=acmetocasino
USERS?=3
HATCH_RATE?=1
RUN_TIME?=120
LOG_LEVEL?=DEBUG
LOCUST_MASTER_HOST?=""
LOCUST_USER?=RobotSalami
BET_OFFER_ID?=2028123865
HOST?=https://acmetocasino-api.teste.workers.dev
USE_PREREG?=false

BETTING_PROVIDER_PLAYER_URL?=https://acmetocasino-api.teste.workers.dev/bettingprovider/auth
BETTING_PROVIDER_OFFERING_URL?=https://acmetocasino-api.teste.workers.dev/bettingprovider/offering
ACME_BASE_URL?=https://acmetocasino-api.teste.workers.dev

push:
	docker push $(IMAGE):$(TAG)

build:
	LOCUST_USER=$(LOCUST_USER) \
		BETTING_PROVIDER_OFFERING_URL=$(BETTING_PROVIDER_OFFERING_URL) \
		ACME_BASE_URL=$(ACME_BASE_URL) \
		BETTING_PROVIDER_PLAYER_URL=${BETTING_PROVIDER_PLAYER_URL} \
		BETTING_PROVIDER_OFFERING_ID=${BETTING_PROVIDER_OFFERING_ID} \
		USERS=$(USERS) \
		HATCH_RATE=$(HATCH_RATE) \
		RUN_TIME=$(RUN_TIME) \
		HOST=$(HOST) \
		TARGET_URL=$(HOST) \
		LOG_LEVEL=$(LOG_LEVEL) \
		LOCUST_MASTER_HOST=$(LOCUST_MASTER_HOST) \
		BET_OFFER_ID=$(BET_OFFER_ID) \
		USE_PREREG=${USE_PREREG} \
		docker-compose build

run: build
	LOCUST_USER=$(LOCUST_USER) \
		BETTING_PROVIDER_OFFERING_URL=$(BETTING_PROVIDER_OFFERING_URL) \
		ACME_BASE_URL=$(ACME_BASE_URL) \
		BETTING_PROVIDER_PLAYER_URL=${BETTING_PROVIDER_PLAYER_URL} \
		BETTING_PROVIDER_OFFERING_ID=${BETTING_PROVIDER_OFFERING_ID} \
		USERS=$(USERS) \
		HATCH_RATE=$(HATCH_RATE) \
		RUN_TIME=$(RUN_TIME) \
		HOST=http://localhost:8000 \
		TARGET_URL=$(HOST) \
		LOG_LEVEL=$(LOG_LEVEL) \
		LOCUST_MASTER_HOST=$(LOCUST_MASTER_HOST) \
		BET_OFFER_ID=$(BET_OFFER_ID) \
		USE_PREREG=${USE_PREREG} \
		docker-compose up --scale worker=$(WORKERS)

dev:
	AWS_PROFILE=$(AWS_PROFILE) \
		DJANGO_SETTINGS_MODULE=$(DJANGO_SETTINGS_MODULE) \
		ENV=$(ENV) \
		NAME=$(NAME) \
		PROPERTY=$(PROPERTY) \
		LOCUST_USER=$(LOCUST_USER) \
		BETTING_PROVIDER_OFFERING_URL=$(BETTING_PROVIDER_OFFERING_URL) \
		ACME_BASE_URL=$(ACME_BASE_URL) \
		BETTING_PROVIDER_PLAYER_URL=${BETTING_PROVIDER_PLAYER_URL} \
		BETTING_PROVIDER_OFFERING_ID=${BETTING_PROVIDER_OFFERING_ID} \
		USERS=$(USERS) \
		HATCH_RATE=$(HATCH_RATE) \
		RUN_TIME=$(RUN_TIME) \
		HOST=$(HOST) \
		TARGET_URL=$(HOST) \
		LOG_LEVEL=$(LOG_LEVEL) \
		LOCUST_MASTER_HOST=$(LOCUST_MASTER_HOST) \
		BET_OFFER_ID=$(BET_OFFER_ID) \
		docker-compose -f docker-compose.local.yml up --scale worker=1
		docker-compose up --scale worker=$(WORKERS)

lint:
	black **/*.py

kube-apply:
	kubectl apply -f deployments/ -n locust

kube-cycle-deployment:
	kubectl patch \
		deployment locust-master \
		-n locust \
		-p "{\"spec\":{\"template\":{\"metadata\":{\"labels\":{\"date\":\"${shell date +%s}\"}}}}}"
	kubectl patch \
		deployment locust-worker \
		-n locust \
		-p "{\"spec\":{\"template\":{\"metadata\":{\"labels\":{\"date\":\"${shell date +%s}\"}}}}}"
docker-push:
	docker-compose push

scale-down-pods:
	kubectl scale deployment locust-master locust-worker -n locust --replicas=0

format-jenkinsfile:
	npm-groovy-lint -f "**.jenkins/**" --format
