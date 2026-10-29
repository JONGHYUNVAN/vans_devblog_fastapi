"""라우터가 Depends 로 받는 객체를 조립한다. 연결은 lifespan 이 app.state 에 만들어 둔 것을 쓴다."""

from functools import partial
from typing import Annotated

from elasticsearch import Elasticsearch
from fastapi import Depends, Request
from pymongo import MongoClient

from app.clients.es import is_es_reachable
from app.clients.mongo import is_mongo_reachable
from app.services.health import HealthService


def get_es(request: Request) -> Elasticsearch:
    return request.app.state.es


def get_mongo_client(request: Request) -> MongoClient:
    return request.app.state.mongo_client


def get_health_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
) -> HealthService:
    return HealthService(
        is_es_reachable=partial(is_es_reachable, es),
        is_mongo_reachable=partial(is_mongo_reachable, mongo_client),
    )
