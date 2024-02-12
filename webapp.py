import hashlib
import hmac
import json
import argparse
import time

import uvicorn
import dataclasses

from starlette.middleware.cors import CORSMiddleware

from database import *
from load_config import *
from BaseLoader import BaseLoader
from fastapi import FastAPI, APIRouter, UploadFile, HTTPException
from pydantic import BaseModel, field_validator, validator
from typing import List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from urllib.parse import parse_qs, unquote, urlencode, quote_plus
from starlette.responses import JSONResponse

TELEGRAM_DATA_HEADER = 'telegram-data'


class FetchPollPayload(BaseModel):
    poll_id: int


class VoteRequestPayload(BaseModel):
    poll_id: int
    votes: List[int]


class VerifyMiddleware(BaseHTTPMiddleware):
    # how many seconds auth tokens are valid for
    AUTH_TOKEN_EXPIRY = 24 * 3600

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            # skip authentication checks for preflight CORS requests
            return await call_next(request)

        # print('PRE-REQUEST', request.headers)
        telegram_data_header = request.headers.get(TELEGRAM_DATA_HEADER)

        if not telegram_data_header:
            # print('HEADERS_NO_THERE', request.headers)
            content = {'detail': 'Missing telegram-data header'}
            return JSONResponse(content=content, status_code=401)

        if PRODUCTION_MODE:
            # only allow auth headers that were created in the last 24 hours
            parsed_query = parse_qs(telegram_data_header)

            try:
                auth_stamp = int(parsed_query.get('auth_date')[0])
            except (KeyError, ValueError) as e:
                content = {'detail': 'Auth date not found or invalid'}
                return JSONResponse(content=content, status_code=400)

            current_stamp = time.time()
            if abs(current_stamp - auth_stamp) > self.AUTH_TOKEN_EXPIRY:
                content = {'detail': 'Auth token expired'}
                return JSONResponse(content=content, status_code=401)

        user_params = self.check_authorization(
            telegram_data_header, TELEGRAM_BOT_TOKEN
        )

        if user_params is None:
            content = {'detail': 'Unauthorized'}
            return JSONResponse(content=content, status_code=401)

        request.state.user = user_params
        return await call_next(request)

    @classmethod
    def parse_auth_string(cls, init_data: str):
        params = parse_qs(init_data)
        # print('AUTH_PARAMS', params)
        signature = params.get('hash', [None])[0]
        if signature is None:
            return None

        auth_param_items = []
        for item in params.items():
            param_key, param_value = item
            if param_key not in ('auth_date', 'query_id', 'user'):
                continue

            auth_param_items.append(item)

        auth_param_items = sorted(auth_param_items)
        data_check_string = "\n".join(
            f"{k}={v[0]}" for k, v in auth_param_items
        )
        return data_check_string, signature, params

    @classmethod
    def check_authorization(
        cls, init_data: str, bot_token: str
    ) -> Optional[dict]:
        parse_result = cls.parse_auth_string(init_data)
        # print('PARSE_RESULT', parse_result)
        data_check_string, signature, params = parse_result

        secret_key = hmac.new(
            key=b"WebAppData", msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        validation_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        # print('VALIDATION_HASH', validation_hash, signature)
        if validation_hash == signature:
            return {k: v[0] for k, v in params.items()}

        return None


class VotingWebApp(BaseLoader):
    def __init__(self):
        self.router = APIRouter()
        self.router.add_api_route(
            '/fetch_poll', self.fetch_poll, methods=['POST']
        )
        self.router.add_api_route(
            '/vote', self.cast_vote, methods=['POST']
        )

    def fetch_poll(self, request: Request, payload: FetchPollPayload):
        telegram_data_header = request.headers.get(TELEGRAM_DATA_HEADER)
        parsed_query = parse_qs(telegram_data_header)
        user_json_str = unquote(parsed_query['user'][0])
        user_info = json.loads(user_json_str)

        chat_username = user_info['username']
        read_poll_result = self.read_poll_info(
            poll_id=payload.poll_id, chat_username=chat_username
        )

        if read_poll_result.is_err():
            error = read_poll_result.err()
            return JSONResponse(
                status_code=500, content={'error': error.get_content()}
            )

        poll_info = read_poll_result.ok()
        return dataclasses.asdict(poll_info)

    def cast_vote(self, payload: VoteRequestPayload):
        raise NotImplementedError


app = FastAPI()
predictor = VotingWebApp()
app.include_router(predictor.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rcvdev.milselarch.com",
        "https://rcvprod.milselarch.com",
        "http://localhost:5001"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(VerifyMiddleware)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='voting web app')
    parser.add_argument(
        '--host', type=str,
        default='0.0.0.0', help='web app host'
    )
    parser.add_argument(
        '--port', type=int,
        default=5010, help='web app port'
    )

    parse_args = parser.parse_args()
    uvicorn.run(app, host=parse_args.host, port=parse_args.port)